from __future__ import annotations

from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.integrations.providers import (
    EXCLUSIVE_ACTIVE_IDENTITY_PROVIDERS,
    INSTAGRAM_DM_AUTH_MODES,
    INSTAGRAM_DM_PROVIDER,
    INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE,
    TIKTOK_DM_PROVIDER,
    get_provider_requirements,
)
from app.models import (
    IntegrationAccount,
    IntegrationAccountAuditAction,
    IntegrationAccountConnectionStatus,
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


class IntegrationAccountLifecycleStateError(ValueError):
    """Raised when legacy account activation would bypass connection lifecycle."""


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
        *,
        actor_user_id: UUID | None = None,
    ) -> tuple[IntegrationAccount, str]:
        validated_secret_reference = self.secret_reference_policy.validate(
            secret_reference
        )
        normalized_provider = provider.strip().lower()
        normalized_auth_mode = self._normalize_provider_auth_mode(
            normalized_provider,
            provider_auth_mode,
        )
        normalized_external_account_id = (
            external_account_id.strip() if external_account_id is not None else None
        )
        self._validate_provider_configuration(
            normalized_provider,
            normalized_auth_mode,
            normalized_external_account_id,
        )
        requirements = get_provider_requirements(
            normalized_provider,
            normalized_auth_mode,
        )
        assert requirements is not None  # Validated immediately above.
        initial_active = not requirements.external_identity_required
        disconnected = self._find_reusable_disconnected_account(
            workspace,
            normalized_provider,
            normalized_auth_mode,
            normalized_external_account_id,
        )
        if disconnected is not None:
            return self._reconfigure_disconnected_account(
                disconnected,
                validated_secret_reference,
                actor_user_id=actor_user_id,
            )
        self._require_provider_identity_available(
            normalized_provider,
            normalized_external_account_id,
            active=initial_active,
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
            active=initial_active,
        )
        self.session.add(account)
        self.audit_service.record(
            account,
            IntegrationAccountAuditAction.CONFIGURED,
            actor_user_id=actor_user_id,
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if normalized_provider in EXCLUSIVE_ACTIVE_IDENTITY_PROVIDERS:
                try:
                    self._require_provider_identity_available(
                        normalized_provider,
                        normalized_external_account_id,
                        active=initial_active,
                    )
                except IntegrationAccountOwnershipConflictError as conflict:
                    raise conflict from exc
            raise
        self.session.refresh(account)
        return account, credential

    def _find_reusable_disconnected_account(
        self,
        workspace: Workspace,
        provider: str,
        provider_auth_mode: str | None,
        external_account_id: str | None,
    ) -> IntegrationAccount | None:
        """Reuse one exact disconnected workspace identity without merging history."""

        if external_account_id is None:
            return None
        matches = list(
            self.session.exec(
                select(IntegrationAccount).where(
                    IntegrationAccount.workspace_id == workspace.id,
                    IntegrationAccount.provider == provider,
                    IntegrationAccount.provider_auth_mode == provider_auth_mode,
                    IntegrationAccount.external_account_id == external_account_id,
                    IntegrationAccount.connection_status
                    == IntegrationAccountConnectionStatus.DISCONNECTED,
                )
            ).all()
        )
        if len(matches) > 1:
            raise IntegrationAccountOwnershipConflictError(
                "Multiple disconnected integration accounts match this provider identity"
            )
        return matches[0] if matches else None

    def _reconfigure_disconnected_account(
        self,
        account: IntegrationAccount,
        secret_reference: str,
        *,
        actor_user_id: UUID | None,
    ) -> tuple[IntegrationAccount, str]:
        credential = self._new_credential()
        account.secret_reference = secret_reference
        account.credential_hash = self._hash_credential(credential)
        account.active = False
        account.connection_status = IntegrationAccountConnectionStatus.CONFIGURED
        account.last_validated_at = None
        account.reconnect_required_at = None
        account.last_connection_error_code = None
        account.updated_at = utc_now()
        self.session.add(account)
        self.audit_service.record(
            account,
            IntegrationAccountAuditAction.CONFIGURED,
            actor_user_id=actor_user_id,
            reason_code="connection_reconfigured",
        )
        self.session.commit()
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

    def deactivate(
        self,
        workspace: Workspace,
        account_id: UUID,
        *,
        actor_user_id: UUID | None = None,
    ) -> IntegrationAccount:
        account = self.get_for_workspace(workspace, account_id)
        account.active = False
        return self._save(
            account,
            IntegrationAccountAuditAction.DEACTIVATED,
            actor_user_id=actor_user_id,
        )

    def reactivate(
        self,
        workspace: Workspace,
        account_id: UUID,
        *,
        actor_user_id: UUID | None = None,
    ) -> IntegrationAccount:
        account = self.get_for_workspace(workspace, account_id)
        if account.connection_status != IntegrationAccountConnectionStatus.CONNECTED:
            raise IntegrationAccountLifecycleStateError(
                "Only connected integration accounts can be reactivated"
            )
        self._require_provider_identity_available(
            account.provider,
            account.external_account_id,
            active=True,
            exclude_account_id=account.id,
        )
        account.active = True
        try:
            return self._save(
                account,
                IntegrationAccountAuditAction.REACTIVATED,
                actor_user_id=actor_user_id,
            )
        except IntegrityError as exc:
            self.session.rollback()
            if account.provider in EXCLUSIVE_ACTIVE_IDENTITY_PROVIDERS:
                try:
                    self._require_provider_identity_available(
                        account.provider,
                        account.external_account_id,
                        active=True,
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
        actor_user_id: UUID | None = None,
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
            actor_user_id=actor_user_id,
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
        *,
        actor_user_id: UUID | None = None,
    ) -> tuple[IntegrationAccount, str]:
        account = self.get_for_workspace(workspace, account_id)
        credential = self._new_credential()
        account.credential_hash = self._hash_credential(credential)
        self._save(
            account,
            IntegrationAccountAuditAction.CREDENTIAL_ROTATED,
            actor_user_id=actor_user_id,
        )
        return account, credential

    def update_secret_reference(
        self,
        workspace: Workspace,
        account_id: UUID,
        secret_reference: str,
        *,
        actor_user_id: UUID | None = None,
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
            actor_user_id=actor_user_id,
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

    def require_active_identity_available(
        self,
        account: IntegrationAccount,
    ) -> None:
        """Fail when enabling this account would duplicate an active channel owner."""

        self._require_provider_identity_available(
            account.provider,
            account.external_account_id,
            active=True,
            exclude_account_id=account.id,
        )

    def _save(
        self,
        account: IntegrationAccount,
        audit_action: IntegrationAccountAuditAction,
        *,
        actor_user_id: UUID | None = None,
        credential_purpose: str | None = None,
        reason_code: str | None = None,
    ) -> IntegrationAccount:
        account.updated_at = utc_now()
        self.session.add(account)
        self.audit_service.record(
            account,
            audit_action,
            actor_user_id=actor_user_id,
            credential_purpose=credential_purpose,
            reason_code=reason_code,
        )
        self.session.commit()
        self.session.refresh(account)
        return account

    @staticmethod
    def _validate_provider_configuration(
        provider: str,
        provider_auth_mode: str | None,
        external_account_id: str | None,
    ) -> None:
        requirements = get_provider_requirements(provider, provider_auth_mode)
        if requirements is None:
            raise IntegrationAccountProviderValidationError(
                "Unsupported integration provider"
            )
        if requirements.external_identity_required and not external_account_id:
            raise IntegrationAccountProviderValidationError(
                "External provider account identifier is required"
            )

    def _require_provider_identity_available(
        self,
        provider: str,
        external_account_id: str | None,
        *,
        active: bool,
        exclude_account_id: UUID | None = None,
    ) -> None:
        if (
            not active
            or provider not in EXCLUSIVE_ACTIVE_IDENTITY_PROVIDERS
            or not external_account_id
        ):
            return
        statement = select(IntegrationAccount.id).where(
            IntegrationAccount.provider == provider,
            IntegrationAccount.external_account_id == external_account_id,
            IntegrationAccount.active.is_(True),
        )
        if exclude_account_id is not None:
            statement = statement.where(IntegrationAccount.id != exclude_account_id)
        if self.session.exec(statement).first() is not None:
            raise IntegrationAccountOwnershipConflictError(
                "Provider account already has an active HIRI owner"
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
