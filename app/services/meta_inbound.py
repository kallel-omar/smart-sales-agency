"""Authenticated Meta inbound normalization and external identity persistence."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.integrations.providers import (
    FACEBOOK_MESSENGER_PROVIDER,
    INSTAGRAM_DM_PROVIDER,
)
from app.models import (
    Contact,
    InboundExternalIdentity,
    IntegrationAccount,
    Lead,
    Workspace,
    utc_now,
)
from app.services.customer_contacts import CustomerContactService
from app.services.inbound_integrations import (
    InboundEventReservation,
    InboundIntegrationService,
)


class MetaInboundNormalizationError(ValueError):
    pass


class MetaWebhookVerificationError(PermissionError):
    pass


def verify_meta_webhook_challenge(
    *,
    mode: str | None,
    verify_token: str | None,
    challenge: str | None,
    configured_verify_token: str | None,
) -> str:
    if (
        mode != "subscribe"
        or not verify_token
        or not challenge
        or not configured_verify_token
        or not hmac.compare_digest(verify_token, configured_verify_token)
    ):
        raise MetaWebhookVerificationError("Meta webhook verification failed")
    return challenge


class MetaInboundAccountMismatchError(MetaInboundNormalizationError):
    pass


class AmbiguousExternalIdentityLeadError(ValueError):
    pass


class InboundExternalIdentityBindingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MetaInboundEvent:
    kind: Literal["direct_message", "comment"]
    channel: str
    provider_event_id: str
    sender_external_id: str
    recipient_account_id: str
    content: str
    display_name: str | None = None
    timestamp: int | None = None
    post_or_media_id: str | None = None
    parent_comment_id: str | None = None
    message_type: str | None = None


class MetaInboundNormalizer:
    def normalize(
        self,
        payload: dict[str, Any],
        *,
        provider: str,
        expected_account_id: str | None,
    ) -> MetaInboundEvent:
        expected_object = {
            FACEBOOK_MESSENGER_PROVIDER: "page",
            INSTAGRAM_DM_PROVIDER: "instagram",
        }.get(provider)
        if expected_object is None or payload.get("object") != expected_object:
            raise MetaInboundNormalizationError("Unexpected Meta webhook object")
        entries = payload.get("entry")
        if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
            raise MetaInboundNormalizationError("Meta webhook has no valid entry")
        entry = entries[0]
        account_id = self._text(entry.get("id"), "Meta account identifier is missing")
        if expected_account_id is None or account_id != expected_account_id:
            raise MetaInboundAccountMismatchError("Meta account reference does not match")
        messaging = entry.get("messaging")
        if isinstance(messaging, list) and messaging:
            return self._direct_message(provider, account_id, messaging[0])
        changes = entry.get("changes")
        if isinstance(changes, list) and changes:
            return self._comment(provider, account_id, changes[0], entry.get("time"))
        raise MetaInboundNormalizationError("Meta webhook contains no supported event")

    def _direct_message(
        self, provider: str, account_id: str, value: Any
    ) -> MetaInboundEvent:
        if not isinstance(value, dict):
            raise MetaInboundNormalizationError("Meta messaging event is invalid")
        sender = value.get("sender")
        recipient = value.get("recipient")
        message = value.get("message")
        if not isinstance(sender, dict) or not isinstance(recipient, dict) or not isinstance(message, dict):
            raise MetaInboundNormalizationError("Meta message identity is missing")
        recipient_id = self._text(recipient.get("id"), "Meta recipient is missing")
        if recipient_id != account_id:
            raise MetaInboundAccountMismatchError("Meta recipient does not match")
        if message.get("is_echo") is True:
            raise MetaInboundNormalizationError("Meta echo messages are not inbound")
        channel = provider
        return MetaInboundEvent(
            kind="direct_message",
            channel=channel,
            provider_event_id=self._text(message.get("mid"), "Meta message id is missing"),
            sender_external_id=self._text(sender.get("id"), "Meta sender is missing"),
            recipient_account_id=account_id,
            content=self._text(message.get("text"), "Meta message text is missing"),
            display_name=self._optional_text(sender.get("name")),
            timestamp=self._timestamp(value.get("timestamp")),
            message_type="text",
        )

    def _comment(
        self, provider: str, account_id: str, change: Any, entry_time: Any
    ) -> MetaInboundEvent:
        if not isinstance(change, dict) or not isinstance(change.get("value"), dict):
            raise MetaInboundNormalizationError("Meta comment event is invalid")
        value = change["value"]
        if provider == FACEBOOK_MESSENGER_PROVIDER:
            if change.get("field") != "feed" or value.get("item") != "comment":
                raise MetaInboundNormalizationError("Unsupported Facebook event")
            author = value.get("from")
            if not isinstance(author, dict):
                raise MetaInboundNormalizationError("Facebook comment author is missing")
            return MetaInboundEvent(
                kind="comment",
                channel="facebook_comment",
                provider_event_id=self._text(value.get("comment_id"), "Facebook comment id is missing"),
                sender_external_id=self._text(author.get("id"), "Facebook comment author is missing"),
                recipient_account_id=account_id,
                content=self._text(value.get("message"), "Facebook comment text is missing"),
                display_name=self._optional_text(author.get("name")),
                timestamp=self._timestamp(value.get("created_time") or entry_time),
                post_or_media_id=self._optional_text(value.get("post_id")),
                parent_comment_id=self._optional_text(value.get("parent_id")),
            )
        if change.get("field") != "comments":
            raise MetaInboundNormalizationError("Unsupported Instagram event")
        author = value.get("from")
        if not isinstance(author, dict):
            raise MetaInboundNormalizationError("Instagram comment author is missing")
        media = value.get("media")
        return MetaInboundEvent(
            kind="comment",
            channel="instagram_comment",
            provider_event_id=self._text(value.get("id"), "Instagram comment id is missing"),
            sender_external_id=self._text(author.get("id"), "Instagram comment author is missing"),
            recipient_account_id=account_id,
            content=self._text(value.get("text"), "Instagram comment text is missing"),
            display_name=self._optional_text(author.get("username")),
            timestamp=self._timestamp(value.get("timestamp") or entry_time),
            post_or_media_id=(
                self._optional_text(media.get("id")) if isinstance(media, dict) else None
            ),
            parent_comment_id=self._optional_text(value.get("parent_id")),
        )

    @staticmethod
    def _text(value: Any, message: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise MetaInboundNormalizationError(message)
        return value.strip()

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _timestamp(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        raise MetaInboundNormalizationError("Meta timestamp is invalid")


class InboundExternalIdentityService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        channel: str,
        external_subject_id: str,
    ) -> InboundExternalIdentity | None:
        self._validate_account(workspace, account)
        identity = self.session.exec(
            select(InboundExternalIdentity).where(
                InboundExternalIdentity.workspace_id == workspace.id,
                InboundExternalIdentity.integration_account_id == account.id,
                InboundExternalIdentity.channel == channel,
                InboundExternalIdentity.external_subject_id == external_subject_id,
            )
        ).first()
        if identity is not None:
            self._validate_targets(workspace, identity.contact_id, identity.lead_id)
        return identity

    def get_or_create_anchor(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        event: MetaInboundEvent,
    ) -> InboundExternalIdentity:
        self._validate_account(workspace, account)
        existing = self.get(
            workspace, account, event.channel, event.sender_external_id
        )
        if existing is not None:
            return existing

        contact = CustomerContactService(self.session).create_external_identity_contact(
            workspace,
            name=event.display_name,
        )
        identity = InboundExternalIdentity(
            workspace_id=workspace.id,
            integration_account_id=account.id,
            channel=event.channel,
            external_subject_id=event.sender_external_id,
            contact_id=contact.id,
            lead_id=None,
        )
        self.session.add(identity)
        try:
            self.session.commit()
            self.session.refresh(identity)
            return identity
        except IntegrityError:
            self.session.rollback()
            winner = self.get(
                workspace, account, event.channel, event.sender_external_id
            )
            self._delete_losing_contact(contact.id, winner)
            if winner is None:
                raise
            return winner

    def bind_captured_identity(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        *,
        channel: str,
        external_subject_id: str,
        contact_id: UUID,
        lead_id: UUID,
    ) -> InboundExternalIdentity:
        """Persist channel/account context for an identity captured by another inbound adapter."""

        self._validate_account(workspace, account)
        self._validate_targets(workspace, contact_id, lead_id)
        existing = self.get(workspace, account, channel, external_subject_id)
        if existing is not None:
            if existing.contact_id != contact_id or existing.lead_id not in {None, lead_id}:
                raise AmbiguousExternalIdentityLeadError(
                    "External identity is already linked to another target"
                )
            existing.lead_id = lead_id
            existing.updated_at = utc_now()
            self.session.add(existing)
            self.session.commit()
            self.session.refresh(existing)
            return existing

        identity = InboundExternalIdentity(
            workspace_id=workspace.id,
            integration_account_id=account.id,
            channel=channel,
            external_subject_id=external_subject_id,
            contact_id=contact_id,
            lead_id=lead_id,
        )
        self.session.add(identity)
        try:
            self.session.commit()
            self.session.refresh(identity)
            return identity
        except IntegrityError:
            self.session.rollback()
            winner = self.get(workspace, account, channel, external_subject_id)
            if (
                winner is None
                or winner.contact_id != contact_id
                or winner.lead_id not in {None, lead_id}
            ):
                raise
            return self.bind_captured_identity(
                workspace,
                account,
                channel=channel,
                external_subject_id=external_subject_id,
                contact_id=contact_id,
                lead_id=lead_id,
            )

    def prepare_for_capture(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        event: MetaInboundEvent,
        integration_service: InboundIntegrationService,
        reservation: InboundEventReservation,
    ) -> tuple[InboundExternalIdentity, Lead | None]:
        """Create/recover the anchor and release the receipt on pre-capture failure."""
        try:
            identity = self.get_or_create_anchor(workspace, account, event)
            return identity, self.resolve_lead(workspace, account, identity)
        except Exception:
            integration_service.release_event_reservation(
                workspace, account, reservation
            )
            raise

    def resolve_lead(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        identity: InboundExternalIdentity,
    ) -> Lead | None:
        self._validate_identity_scope(workspace, account, identity)
        if identity.lead_id is not None:
            self._validate_targets(workspace, identity.contact_id, identity.lead_id)
            return self.session.get(Lead, identity.lead_id)
        matches = list(
            self.session.exec(
                select(Lead).where(
                    Lead.tenant_id == workspace.slug,
                    Lead.contact_id == identity.contact_id,
                )
            ).all()
        )
        if len(matches) > 1:
            raise AmbiguousExternalIdentityLeadError(
                "External identity has multiple linked Leads"
            )
        if not matches:
            return None
        self.bind_lead(workspace, account, identity, matches[0].id)
        return matches[0]

    def bind_lead(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        identity: InboundExternalIdentity,
        lead_id: UUID,
    ) -> InboundExternalIdentity:
        self._validate_identity_scope(workspace, account, identity)
        self._validate_targets(workspace, identity.contact_id, lead_id)
        for attempt in range(2):
            current = self.session.exec(
                select(InboundExternalIdentity).where(
                    InboundExternalIdentity.id == identity.id,
                    InboundExternalIdentity.workspace_id == workspace.id,
                    InboundExternalIdentity.integration_account_id == account.id,
                )
            ).first()
            if current is None:
                raise MetaInboundAccountMismatchError("External identity not found")
            if current.lead_id == lead_id:
                return current
            if current.lead_id is not None:
                raise AmbiguousExternalIdentityLeadError(
                    "External identity is already linked to another Lead"
                )
            current.lead_id = lead_id
            try:
                self.session.add(current)
                self.session.commit()
                self.session.refresh(current)
                return current
            except Exception as exc:
                self.session.rollback()
                if attempt == 1:
                    raise InboundExternalIdentityBindingError(
                        "External identity Lead binding failed"
                    ) from exc
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_account(workspace: Workspace, account: IntegrationAccount) -> None:
        if account.workspace_id != workspace.id:
            raise MetaInboundAccountMismatchError("Integration account not found")

    def _validate_targets(self, workspace: Workspace, contact_id, lead_id) -> None:
        contact = self.session.exec(
            select(Contact).where(
                Contact.id == contact_id, Contact.workspace_id == workspace.id
            )
        ).first()
        if contact is None:
            raise MetaInboundAccountMismatchError("External identity target not found")
        if lead_id is not None:
            lead = self.session.exec(
                select(Lead).where(Lead.id == lead_id, Lead.tenant_id == workspace.slug)
            ).first()
            if lead is None or lead.contact_id != contact.id:
                raise MetaInboundAccountMismatchError("External identity target not found")

    def _validate_identity_scope(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        identity: InboundExternalIdentity,
    ) -> None:
        self._validate_account(workspace, account)
        if (
            identity.workspace_id != workspace.id
            or identity.integration_account_id != account.id
        ):
            raise MetaInboundAccountMismatchError("External identity not found")

    def _delete_losing_contact(
        self,
        contact_id: UUID,
        winner: InboundExternalIdentity | None,
    ) -> None:
        if winner is not None and winner.contact_id == contact_id:
            return
        contact = self.session.get(Contact, contact_id)
        if contact is not None:
            self.session.delete(contact)
            self.session.commit()
