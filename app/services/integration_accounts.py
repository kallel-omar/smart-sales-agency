from __future__ import annotations

from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.integrations.providers import (
    INSTAGRAM_DM_AUTH_MODES,
    INSTAGRAM_DM_PROVIDER,
    INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE,
    TIKTOK_DM_PROVIDER,
)
from app.models import (
    IntegrationAccount,
    IntegrationAccountAuditAction,
    Workspace,
    utc_now,
)
from app.services.integration_account_audit import IntegrationAccountAuditService
from app.services.secret_reference_policy import IntegrationSecretReferencePolicy


class IntegrationAccountNotFoundError(LookupError):
    """Raised when an account is absent from the requesting workspace."""


class IntegrationAccountProviderAuthModeError(ValueError):
    """Raised when provider authentication routing is unsupported."""


class IntegrationAccountProviderValidationError(ValueError):
    """Raised when provider-owned non-secret configuration is invalid."""


class IntegrationAccountOwnershipConflictError(ValueError):
    """Raised when an active provider identity already belongs to HIRI."""


class IntegrationAccountRoutingError(LookupError):
    """Raised when an inbound provider identity cannot resolve safely."""


class IntegrationAccountService:
    """Workspace-scoped lifecycle operations for inbound integration accounts."""

    def __init__(
        self,
        session: Session,
        secret_reference_policy: IntegrationSecretReferencePolicy | None = None,
    ) -> None:
        self.session = session
        self.secret_reference_policy = (
            secret_reference_policy or IntegrationSecretReferencePolicy()
        )
        self.audit_service = IntegrationAccountAuditService(session)

    def provision(
        self,
        workspace: Workspace,
        provider: str,
        external_account_id: str | None,
        secret_reference: str,
        provider_auth_mode: str | None = None,
    ) -> tuple[IntegrationAccount, str]:
        validated_secret_reference = self.secret_reference_policy.validate(
            secret_reference
        )
        normalized_provider = provider.strip()
        normalized_auth_mode = self._normalize_provider_auth_mode(
            normalized_provider,
            provider_auth_mode,
        )
        normalized_external_account_id = (
            external_account_id.strip() if external_account_id is not None else None
        )
        self._validate_provider_configuration(
            normalized_provider,
            normalized_external_account_id,
        )
        self._require_tiktok_identity_available(
            normalized_provider,
            normalized_external_account_id,
        )
        credential = self._new_credential()
        account = IntegrationAccount(
            workspace_id=workspace.id,
            provider=normalized_provider,
            external_account_id=normalized_external_account_id,
            provider_auth_mode=normalized_auth_mode,
            comment_to_message_eligible=False,
            secret_reference=validated_secret_reference,
            credential_hash=self._hash_credential(credential),
        )
        self.session.add(account)
        self.audit_service.record(account, IntegrationAccountAuditAction.PROVISIONED)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if normalized_provider == TIKTOK_DM_PROVIDER:
                try:
                    self._require_tiktok_identity_available(
                        normalized_provider,
                        normalized_external_account_id,
                    )
                except IntegrationAccountOwnershipConflictError as conflict:
                    raise conflict from exc
            raise
        self.session.refresh(account)
        return account, credential

    @staticmethod
    def _normalize_provider_auth_mode(
        provider: str,
        provider_auth_mode: str | None,
    ) -> str | None:
        if provider != INSTAGRAM_DM_PROVIDER:
            if provider_auth_mode is not None:
                raise IntegrationAccountProviderAuthModeError(
                    "Provider authentication mode is only supported for instagram_dm"
                )
            return None

        if provider_auth_mode is None:
            return INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE

        normalized = provider_auth_mode.strip().lower()
        if normalized not in INSTAGRAM_DM_AUTH_MODES:
            raise IntegrationAccountProviderAuthModeError(
                "Unsupported instagram_dm provider authentication mode"
            )
        return normalized

    def list_for_workspace(self, workspace: Workspace) -> list[IntegrationAccount]:
        statement = (
            select(IntegrationAccount)
            .where(IntegrationAccount.workspace_id == workspace.id)
            .order_by(IntegrationAccount.created_at.desc())
        )
        return list(self.session.exec(statement).all())

    def deactivate(self, workspace: Workspace, account_id: UUID) -> IntegrationAccount:
        account = self.get_for_workspace(workspace, account_id)
        account.active = False
        return self._save(account, IntegrationAccountAuditAction.DEACTIVATED)

    def reactivate(self, workspace: Workspace, account_id: UUID) -> IntegrationAccount:
        account = self.get_for_workspace(workspace, account_id)
        self._require_tiktok_identity_available(
            account.provider,
            account.external_account_id,
            exclude_account_id=account.id,
        )
        account.active = True
        try:
            return self._save(account, IntegrationAccountAuditAction.REACTIVATED)
        except IntegrityError as exc:
            self.session.rollback()
            if account.provider == TIKTOK_DM_PROVIDER:
                try:
                    self._require_tiktok_identity_available(
                        account.provider,
                        account.external_account_id,
                        exclude_account_id=account.id,
                    )
                except IntegrationAccountOwnershipConflictError as conflict:
                    raise conflict from exc
            raise

    def set_comment_to_message_eligibility(
        self,
        workspace: Workspace,
        account_id: UUID,
        *,
        eligible: bool,
    ) -> IntegrationAccount:
        account = self.get_for_workspace(workspace, account_id)
        if account.provider != TIKTOK_DM_PROVIDER:
            raise IntegrationAccountProviderValidationError(
                "Comment-to-Message eligibility is only supported for tiktok_dm"
            )
        account.comment_to_message_eligible = eligible
        return self._save(
            account,
            IntegrationAccountAuditAction.COMMENT_TO_MESSAGE_ELIGIBILITY_CHANGED,
        )

    def resolve_active_tiktok_account(self, external_account_id: str) -> IntegrationAccount:
        normalized = external_account_id.strip()
        if not normalized:
            raise IntegrationAccountRoutingError("TikTok integration account is not recognized")
        matches = list(
            self.session.exec(
                select(IntegrationAccount).where(
                    IntegrationAccount.provider == TIKTOK_DM_PROVIDER,
                    IntegrationAccount.external_account_id == normalized,
                    IntegrationAccount.active.is_(True),
                )
            ).all()
        )
        if len(matches) != 1:
            raise IntegrationAccountRoutingError("TikTok integration account is not recognized")
        return matches[0]

    def rotate_credential(
        self,
        workspace: Workspace,
        account_id: UUID,
    ) -> tuple[IntegrationAccount, str]:
        account = self.get_for_workspace(workspace, account_id)
        credential = self._new_credential()
        account.credential_hash = self._hash_credential(credential)
        self._save(account, IntegrationAccountAuditAction.CREDENTIAL_ROTATED)
        return account, credential

    def update_secret_reference(
        self,
        workspace: Workspace,
        account_id: UUID,
        secret_reference: str,
    ) -> IntegrationAccount:
        """Update a validated reference without resolving its value.

        Inactive accounts are intentionally eligible: this changes future
        verifier configuration only and does not reactivate the account.
        """
        account = self.get_for_workspace(workspace, account_id)
        account.secret_reference = self.secret_reference_policy.validate(secret_reference)
        return self._save(
            account,
            IntegrationAccountAuditAction.SECRET_REFERENCE_CHANGED,
        )

    def get_for_workspace(
        self,
        workspace: Workspace,
        account_id: UUID,
    ) -> IntegrationAccount:
        account = self.session.exec(
            select(IntegrationAccount).where(
                IntegrationAccount.id == account_id,
                IntegrationAccount.workspace_id == workspace.id,
            )
        ).first()
        if not account:
            raise IntegrationAccountNotFoundError("Integration account not found")
        return account

    def _save(
        self,
        account: IntegrationAccount,
        audit_action: IntegrationAccountAuditAction,
    ) -> IntegrationAccount:
        account.updated_at = utc_now()
        self.session.add(account)
        self.audit_service.record(account, audit_action)
        self.session.commit()
        self.session.refresh(account)
        return account

    @staticmethod
    def _validate_provider_configuration(
        provider: str,
        external_account_id: str | None,
    ) -> None:
        if provider == TIKTOK_DM_PROVIDER and not external_account_id:
            raise IntegrationAccountProviderValidationError(
                "TikTok Business Account identifier is required"
            )

    def _require_tiktok_identity_available(
        self,
        provider: str,
        external_account_id: str | None,
        *,
        exclude_account_id: UUID | None = None,
    ) -> None:
        if provider != TIKTOK_DM_PROVIDER or not external_account_id:
            return
        statement = select(IntegrationAccount.id).where(
            IntegrationAccount.provider == TIKTOK_DM_PROVIDER,
            IntegrationAccount.external_account_id == external_account_id,
            IntegrationAccount.active.is_(True),
        )
        if exclude_account_id is not None:
            statement = statement.where(IntegrationAccount.id != exclude_account_id)
        if self.session.exec(statement).first() is not None:
            raise IntegrationAccountOwnershipConflictError(
                "TikTok Business Account already has an active HIRI owner"
            )

    def _new_credential(self) -> str:
        """Generate a high-entropy credential whose hash is not already in use."""
        while True:
            credential = token_urlsafe(32)
            credential_hash = self._hash_credential(credential)
            existing = self.session.exec(
                select(IntegrationAccount.id).where(
                    IntegrationAccount.credential_hash == credential_hash
                )
            ).first()
            if not existing:
                return credential

    @staticmethod
    def _hash_credential(credential: str) -> str:
        return sha256(credential.encode()).hexdigest()
