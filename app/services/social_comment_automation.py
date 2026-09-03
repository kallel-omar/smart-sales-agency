from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from app.config import Settings
from app.core.comment_triggers import CommentTriggerResult, InboundCommentChannel
from app.core.lead_capture import LeadCaptureResult, LeadCaptureSignal
from app.integrations.providers import TIKTOK_DM_PROVIDER
from app.models import InboundCommentTriggerRule, IntegrationAccount, Workspace
from app.services.comment_trigger_rules import CommentTriggerRuleService
from app.services.inbound_integrations import (
    InboundEventReservation,
    InboundIntegrationService,
)
from app.services.meta_inbound import (
    InboundExternalIdentityBindingError,
    InboundExternalIdentityService,
)
from app.services.repository import SalesRepository
from app.services.send_message_work_items import (
    SendMessageWorkItemResult,
    SendMessageWorkItemService,
)
from app.services.social_inbound import SocialInboundEvent


@dataclass(frozen=True, slots=True)
class SocialCommentAutomationResult:
    outcome: CommentTriggerResult
    rule: InboundCommentTriggerRule | None = None
    capture: LeadCaptureResult | None = None
    send: SendMessageWorkItemResult | None = None


class SocialCommentAutomationService:
    """Turn only an explicitly matched social comment into governed business work."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def process(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        event: SocialInboundEvent,
        reservation: InboundEventReservation,
        inbound: InboundIntegrationService,
    ) -> SocialCommentAutomationResult:
        if event.sender_external_id == account.external_account_id:
            return SocialCommentAutomationResult(outcome=CommentTriggerResult.NO_MATCH)
        if (
            event.channel == InboundCommentChannel.TIKTOK_COMMENT
            and (
                account.provider != TIKTOK_DM_PROVIDER
                or not account.comment_to_message_eligible
            )
        ):
            return SocialCommentAutomationResult(
                outcome=CommentTriggerResult.PROVIDER_INELIGIBLE
            )
        match = CommentTriggerRuleService(self.session).match(
            workspace,
            account,
            InboundCommentChannel(event.channel),
            event.content,
            event.post_or_media_id,
        )
        if match.ambiguous:
            return SocialCommentAutomationResult(outcome=CommentTriggerResult.AMBIGUOUS)
        if match.rule is None:
            return SocialCommentAutomationResult(outcome=CommentTriggerResult.NO_MATCH)

        identities = InboundExternalIdentityService(self.session)
        identity, recovered_lead = identities.prepare_for_capture(
            workspace,
            account,
            event,
            inbound,
            reservation,
        )
        capture = inbound.capture_reserved_event(
            workspace,
            account,
            reservation,
            LeadCaptureSignal(
                source=event.channel,
                name=event.display_name,
                message=event.content,
                external_reference=event.provider_event_id,
                metadata={
                    "post_or_media_id": event.post_or_media_id,
                    "parent_comment_id": event.parent_comment_id,
                    "timestamp": event.timestamp,
                    "trigger_rule_id": str(match.rule.id),
                },
                contact_id=identity.contact_id,
                lead_id=(recovered_lead.id if recovered_lead is not None else None),
            ),
        )
        try:
            identities.bind_lead(workspace, account, identity, capture.lead_id)
        except InboundExternalIdentityBindingError:
            pass

        if SalesRepository(self.session).get_sales_handoff(workspace, capture.lead_id):
            return SocialCommentAutomationResult(
                outcome=CommentTriggerResult.HANDOFF_ACTIVE,
                rule=match.rule,
                capture=capture,
            )

        rules = CommentTriggerRuleService(self.session)
        assignment, _, capability, department = rules.resolve_send_context(workspace, match.rule)
        safe_input = {
            "lead_id": str(capture.lead_id),
            "contact_id": str(capture.contact_id),
            "trigger_rule_id": str(match.rule.id),
            "integration_account_id": str(account.id),
            "channel": event.channel,
            "comment_id": event.provider_event_id,
            "external_subject_id": event.sender_external_id,
            "message": match.rule.dm_message,
            "post_or_media_id": event.post_or_media_id,
        }
        send = SendMessageWorkItemService(self.session, self.settings).execute(
            workspace,
            department,
            capability,
            assignment,
            account,
            message=match.rule.dm_message,
            external_target_id=event.provider_event_id,
            input=safe_input,
            idempotency_source=(
                f"{workspace.id}:{account.id}:{match.rule.id}:{event.provider_event_id}"
            ),
            correlation_id=reservation.receipt.correlation_id,
        )
        return SocialCommentAutomationResult(
            outcome=send.outcome,
            rule=match.rule,
            capture=capture,
            send=send,
        )
