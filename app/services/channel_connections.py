"""Provider-neutral channel connection lifecycle operations."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    IntegrationAccountAuditAction,
    IntegrationAccountConnectionStatus,
    IntegrationCredentialReference,
    Workspace,
    utc_now,
)
from app.services.integration_account_audit import IntegrationAccountAuditService
from app.services.integration_accounts import IntegrationAccountService

_SAFE_REASON_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class ChannelConnectionLifecycleError(ValueError):
    """Raised when a requested lifecycle transition is not safe."""


@dataclass(frozen=True)
class ChannelConnectionValidationResult:
    """Safe provider-neutral result returned by a provider validator."""

    succeeded: bool
    reason_code: str | None = None
    reconnect_required: bool = False
    temporary_failure: bool = False
    provider_account_identity: str | None = None
    checks_performed: tuple[str, ...] = ()
    checks_passed: tuple[str, ...] = ()
    checks_failed: tuple[str, ...] = ()
    checks_unavailable: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelConnectionValidationOutcome:
    account: IntegrationAccount
    result: ChannelConnectionValidationResult


class ChannelConnectionValidator(Protocol):
    """Narrow provider validation boundary separate from message delivery."""

    def validate(self, account: IntegrationAccount) -> ChannelConnectionValidationResult: ...


class ChannelConnectionValidatorNotFoundError(LookupError):
    """Raised when no approved validator exists for an integration provider."""


class ChannelConnectionValidatorRegistry:
    """Explicit allowlist of provider connection validators."""

    def __init__(self, validators: Mapping[str, ChannelConnectionValidator]) -> None:
        self.validators = dict(validators)

    def get(self, provider: str) -> ChannelConnectionValidator:
        try:
            return self.validators[provider]
        except KeyError as exc:
            raise ChannelConnectionValidatorNotFoundError(
                "Connection validation is not supported for this provider"
            ) from exc


class ChannelConnectionService:
    """Own connection state without owning account CRUD or provider messaging."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.account_service = IntegrationAccountService(session)
        self.audit_service = IntegrationAccountAuditService(session)

    def validate(
        self,
        workspace: Workspace,
        account_id: UUID,
        validator: ChannelConnectionValidator,
        *,
        actor_user_id: UUID | None = None,
    ) -> IntegrationAccount:
        """Apply one deterministic validation result without changing active."""

        return self.validate_with_result(
            workspace,
            account_id,
            validator,
            actor_user_id=actor_user_id,
        ).account

    def validate_with_result(
        self,
        workspace: Workspace,
        account_id: UUID,
        validator: ChannelConnectionValidator,
        *,
        actor_user_id: UUID | None = None,
    ) -> ChannelConnectionValidationOutcome:
        """Apply validation and retain its safe structured provider result."""

        account = self.account_service.get_for_workspace(workspace, account_id)
        if account.connection_status == IntegrationAccountConnectionStatus.DISCONNECTED:
            raise ChannelConnectionLifecycleError(
                "Disconnected integration accounts must be reconfigured before validation"
            )

        result = validator.validate(account)
        if result.succeeded:
            account = self._record_validation_success(
                account,
                actor_user_id=actor_user_id,
            )
        else:
            account = self._record_validation_failure(
                account,
                reason_code=result.reason_code or "connection_validation_failed",
                reconnect_required=result.reconnect_required,
                actor_user_id=actor_user_id,
            )
        return ChannelConnectionValidationOutcome(account=account, result=result)

    def mark_reconnect_required(
        self,
        workspace: Workspace,
        account_id: UUID,
        *,
        reason_code: str,
        actor_user_id: UUID | None = None,
    ) -> IntegrationAccount:
        account = self.account_service.get_for_workspace(workspace, account_id)
        self.apply_reconnect_required(
            account,
            reason_code=reason_code,
            actor_user_id=actor_user_id,
        )
        self.session.commit()
        self.session.refresh(account)
        return account

    def apply_reconnect_required(
        self,
        account: IntegrationAccount,
        *,
        reason_code: str,
        actor_user_id: UUID | None = None,
    ) -> None:
        """Stage reconnect-required state for an already-scoped account."""

        if account.connection_status == IntegrationAccountConnectionStatus.DISCONNECTED:
            return
        safe_reason = self._safe_reason_code(reason_code)
        account.connection_status = IntegrationAccountConnectionStatus.RECONNECT_REQUIRED
        account.reconnect_required_at = utc_now()
        account.last_connection_error_code = safe_reason
        account.updated_at = utc_now()
        self.session.add(account)
        self.audit_service.record(
            account,
            IntegrationAccountAuditAction.RECONNECT_REQUIRED,
            actor_user_id=actor_user_id,
            reason_code=safe_reason,
        )

    def disable(
        self,
        workspace: Workspace,
        account_id: UUID,
        *,
        actor_user_id: UUID | None = None,
    ) -> IntegrationAccount:
        account = self.account_service.get_for_workspace(workspace, account_id)
        if account.active:
            account.active = False
            self._save(
                account,
                IntegrationAccountAuditAction.DISABLED,
                actor_user_id=actor_user_id,
            )
        return account

    def enable(
        self,
        workspace: Workspace,
        account_id: UUID,
        *,
        actor_user_id: UUID | None = None,
    ) -> IntegrationAccount:
        account = self.account_service.get_for_workspace(workspace, account_id)
        if account.connection_status != IntegrationAccountConnectionStatus.CONNECTED:
            raise ChannelConnectionLifecycleError(
                "Only connected integration accounts can be enabled"
            )
        if not account.active:
            self.account_service.require_active_identity_available(account)
            account.active = True
            self._save(
                account,
                IntegrationAccountAuditAction.ENABLED,
                actor_user_id=actor_user_id,
            )
        return account

    def disconnect(
        self,
        workspace: Workspace,
        account_id: UUID,
        *,
        actor_user_id: UUID | None = None,
    ) -> IntegrationAccount:
        """Terminate HIRI's relationship without claiming external revocation."""

        account = self.account_service.get_for_workspace(workspace, account_id)
        if account.connection_status == IntegrationAccountConnectionStatus.DISCONNECTED:
            return account

        references = list(
            self.session.exec(
                select(IntegrationCredentialReference).where(
                    IntegrationCredentialReference.workspace_id == workspace.id,
                    IntegrationCredentialReference.integration_account_id == account.id,
                )
            ).all()
        )
        for reference in references:
            self.audit_service.record(
                account,
                IntegrationAccountAuditAction.CREDENTIAL_REFERENCE_CHANGED,
                actor_user_id=actor_user_id,
                credential_purpose=reference.purpose,
                reason_code="connection_disconnected",
            )
            self.session.delete(reference)

        account.secret_reference = None
        account.active = False
        account.connection_status = IntegrationAccountConnectionStatus.DISCONNECTED
        account.reconnect_required_at = None
        account.last_connection_error_code = None
        self._save(
            account,
            IntegrationAccountAuditAction.DISCONNECTED,
            actor_user_id=actor_user_id,
        )
        return account

    def _record_validation_success(
        self,
        account: IntegrationAccount,
        *,
        actor_user_id: UUID | None,
    ) -> IntegrationAccount:
        previous_status = account.connection_status
        account.connection_status = IntegrationAccountConnectionStatus.CONNECTED
        account.last_validated_at = utc_now()
        account.reconnect_required_at = None
        account.last_connection_error_code = None
        account.updated_at = utc_now()
        self.session.add(account)
        self.audit_service.record(
            account,
            IntegrationAccountAuditAction.VALIDATION_SUCCEEDED,
            actor_user_id=actor_user_id,
        )
        lifecycle_action = None
        if previous_status == IntegrationAccountConnectionStatus.RECONNECT_REQUIRED:
            lifecycle_action = IntegrationAccountAuditAction.RECONNECT_COMPLETED
        elif previous_status == IntegrationAccountConnectionStatus.CONFIGURED:
            lifecycle_action = IntegrationAccountAuditAction.CONNECTED
        if lifecycle_action is not None:
            self.audit_service.record(
                account,
                lifecycle_action,
                actor_user_id=actor_user_id,
            )
        self.session.commit()
        self.session.refresh(account)
        return account

    def _record_validation_failure(
        self,
        account: IntegrationAccount,
        *,
        reason_code: str,
        reconnect_required: bool,
        actor_user_id: UUID | None,
    ) -> IntegrationAccount:
        safe_reason = self._safe_reason_code(reason_code)
        account.last_connection_error_code = safe_reason
        account.updated_at = utc_now()
        self.session.add(account)
        self.audit_service.record(
            account,
            IntegrationAccountAuditAction.VALIDATION_FAILED,
            actor_user_id=actor_user_id,
            reason_code=safe_reason,
        )
        if reconnect_required:
            self.apply_reconnect_required(
                account,
                reason_code=safe_reason,
                actor_user_id=actor_user_id,
            )
        self.session.commit()
        self.session.refresh(account)
        return account

    def _save(
        self,
        account: IntegrationAccount,
        action: IntegrationAccountAuditAction,
        *,
        actor_user_id: UUID | None,
    ) -> None:
        account.updated_at = utc_now()
        self.session.add(account)
        self.audit_service.record(
            account,
            action,
            actor_user_id=actor_user_id,
        )
        self.session.commit()
        self.session.refresh(account)

    @staticmethod
    def _safe_reason_code(value: str) -> str:
        normalized = value.strip().lower()
        if not _SAFE_REASON_CODE_PATTERN.fullmatch(normalized):
            raise ChannelConnectionLifecycleError(
                "Connection reason code must use lowercase letters, numbers, and underscores"
            )
        return normalized
