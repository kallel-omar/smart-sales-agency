"""Explicit orchestration for provider-neutral outbound action delivery."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationAuditAction,
    OutboundIntegrationDeliveryAttempt,
    Workspace,
    utc_now,
)
from app.services.delivery_adapters import (
    DeliveryAdapterRegistry,
    DeliveryAdapterResult,
    GenericWebhookDeliveryAdapter,
    MetaGraphDeliveryAdapter,
    WhatsAppCloudDeliveryAdapter,
    default_delivery_adapter_registry,
)
from app.services.integration_accounts import IntegrationAccountService
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceService,
)
from app.services.outbound_action_audit import OutboundIntegrationActionAuditService
from app.services.outbound_action_state_transitions import (
    OutboundIntegrationActionInvalidStateTransitionError,
    OutboundIntegrationActionStateTransitionGuard,
)
from app.services.outbound_delivery_approvals import OutboundDeliveryApprovalService
from app.services.outbound_integrations import InactiveIntegrationAccountError
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy

if TYPE_CHECKING:
    from app.config import Settings


class OutboundIntegrationActionNotFoundError(LookupError):
    """Raised when an action is absent from the requesting account/workspace."""


class OutboundIntegrationActionAlreadyProcessedError(ValueError):
    """Raised when a terminal action is explicitly submitted for delivery again."""


class OutboundIntegrationActionNotRetryableError(ValueError):
    """Raised when an action is not a failed action eligible for explicit retry."""


class OutboundIntegrationActionRetryDeniedError(OutboundIntegrationActionNotRetryableError):
    """Raised when the retry policy safely denies a failed action."""


class OutboundIntegrationActionNotCancellableError(ValueError):
    """Raised when an action is no longer pending."""


class OutboundIntegrationActionExpiredError(ValueError):
    """Raised after a pending action expires before an adapter is invoked."""


class OutboundIntegrationActionNotReadyError(ValueError):
    """Raised before an action's UTC not-before time is reached."""


class OutboundDeliveryAttemptQueryValidationError(ValueError):
    """Raised when a delivery-attempt read filter has an invalid range."""


DEFAULT_DELIVERY_ATTEMPT_LIMIT = 50
MAX_DELIVERY_ATTEMPT_LIMIT = 100


class OutboundIntegrationDeliveryService:
    """Delivers one pending action and persists only its safe outcome."""

    def __init__(
        self,
        session: Session,
        adapter_registry: DeliveryAdapterRegistry | None = None,
        retry_policy: OutboundDeliveryRetryPolicy | None = None,
    ) -> None:
        self.session = session
        self.account_service = IntegrationAccountService(session)
        self.adapter_registry = adapter_registry or default_delivery_adapter_registry()
        self.retry_policy = retry_policy or OutboundDeliveryRetryPolicy(3)
        self.audit_service = OutboundIntegrationActionAuditService(session)
        self.approval_service = OutboundDeliveryApprovalService(session)
        self.transition_guard = OutboundIntegrationActionStateTransitionGuard()

    @classmethod
    def from_settings(
        cls,
        session: Session,
        settings: "Settings",
        *,
        retry_policy: OutboundDeliveryRetryPolicy | None = None,
    ) -> "OutboundIntegrationDeliveryService":
        webhook_adapter = GenericWebhookDeliveryAdapter(
            settings.outbound_webhook_url,
            connect_timeout_seconds=settings.outbound_webhook_connect_timeout_seconds,
            read_timeout_seconds=settings.outbound_webhook_read_timeout_seconds,
            signing_enabled=settings.outbound_webhook_signing_enabled,
        )
        whatsapp_cloud_adapter = WhatsAppCloudDeliveryAdapter(
            IntegrationCredentialReferenceService(session),
            graph_api_base_url=settings.whatsapp_cloud_graph_api_base_url,
            graph_api_version=settings.whatsapp_cloud_graph_api_version,
            connect_timeout_seconds=settings.whatsapp_cloud_connect_timeout_seconds,
            read_timeout_seconds=settings.whatsapp_cloud_read_timeout_seconds,
        )
        meta_graph_adapter = MetaGraphDeliveryAdapter(
            IntegrationCredentialReferenceService(session),
            graph_api_base_url=settings.meta_graph_api_base_url,
            instagram_graph_api_base_url=settings.instagram_graph_api_base_url,
            graph_api_version=settings.meta_graph_api_version,
            connect_timeout_seconds=settings.meta_graph_connect_timeout_seconds,
            read_timeout_seconds=settings.meta_graph_read_timeout_seconds,
        )
        return cls(
            session,
            adapter_registry=default_delivery_adapter_registry(
                webhook_adapter,
                whatsapp_cloud_adapter,
                meta_graph_adapter,
            ),
            retry_policy=retry_policy,
        )

    def deliver_pending_action(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
    ) -> tuple[OutboundIntegrationAction, IntegrationAccount]:
        account = self.account_service.get_for_workspace(workspace, account_id)
        if not account.active:
            raise InactiveIntegrationAccountError("Integration account is inactive")

        action = self._get_action_for_account(workspace, account, action_id)
        if (
            action.status == OutboundIntegrationActionStatus.PENDING
            and self.is_expired(action)
        ):
            previous_status = action.status
            self.transition_guard.require_transition(
                action, OutboundIntegrationActionStatus.EXPIRED
            )
            action.status = OutboundIntegrationActionStatus.EXPIRED
            action.expired_at = utc_now()
            self.session.add(action)
            self._record_transition_audit(action, previous_status)
            self.session.commit()
            raise OutboundIntegrationActionExpiredError("Outbound integration action has expired")
        try:
            self.transition_guard.require_pending_delivery(action)
        except OutboundIntegrationActionInvalidStateTransitionError as exc:
            raise OutboundIntegrationActionAlreadyProcessedError(
                "Outbound integration action has already reached a terminal state"
            ) from exc
        if self.is_before_not_before(action):
            raise OutboundIntegrationActionNotReadyError(
                "Outbound integration action is not available before its not-before time"
            )

        self.approval_service.require_approved(workspace, action)

        return self._deliver_action(action, account)

    def retry_failed_action(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
    ) -> tuple[OutboundIntegrationAction, IntegrationAccount]:
        """Explicitly retry one failed action without creating a replacement action."""
        account = self.account_service.get_for_workspace(workspace, account_id)
        if not account.active:
            raise InactiveIntegrationAccountError("Integration account is inactive")

        action = self._get_action_for_account(workspace, account, action_id)
        try:
            self.transition_guard.require_retry_attempt(action)
        except OutboundIntegrationActionInvalidStateTransitionError as exc:
            raise OutboundIntegrationActionNotRetryableError(
                "Only failed outbound integration actions can be retried"
            ) from exc
        eligibility = self.retry_policy.evaluate(
            attempt_count=self._attempt_count(action),
            failure_code=action.failure_code,
            failure_classification=action.failure_classification,
        )
        if not eligibility.allowed:
            raise OutboundIntegrationActionRetryDeniedError(
                f"Outbound integration action retry is not eligible: {eligibility.denial_reason}"
            )

        self.audit_service.record(action, OutboundIntegrationAuditAction.RETRIED)
        return self._deliver_action(action, account)

    def cancel_pending_action(
        self, workspace: Workspace, account_id: UUID, action_id: UUID
    ) -> tuple[OutboundIntegrationAction, IntegrationAccount]:
        account = self.account_service.get_for_workspace(workspace, account_id)
        action = self._get_action_for_account(workspace, account, action_id)
        previous_status = action.status
        try:
            self.transition_guard.require_transition(
                action, OutboundIntegrationActionStatus.CANCELLED
            )
        except OutboundIntegrationActionInvalidStateTransitionError as exc:
            raise OutboundIntegrationActionNotCancellableError(
                "Only pending outbound integration actions can be cancelled"
            ) from exc
        action.status = OutboundIntegrationActionStatus.CANCELLED
        action.cancelled_at = utc_now()
        self.session.add(action)
        self._record_transition_audit(action, previous_status)
        self.session.commit()
        self.session.refresh(action)
        return action, account

    def list_attempts_for_action(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
        *,
        attempt_status: OutboundIntegrationActionStatus | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        newest_first: bool = False,
        limit: int = DEFAULT_DELIVERY_ATTEMPT_LIMIT,
    ) -> list[OutboundIntegrationDeliveryAttempt]:
        """Return safe attempt history only after workspace/account/action scoping."""
        if started_after and started_before and started_after > started_before:
            raise OutboundDeliveryAttemptQueryValidationError(
                "started_after must be earlier than or equal to started_before"
            )
        account = self.account_service.get_for_workspace(workspace, account_id)
        self._get_action_for_account(workspace, account, action_id)
        statement = select(OutboundIntegrationDeliveryAttempt).where(
            OutboundIntegrationDeliveryAttempt.workspace_id == workspace.id,
            OutboundIntegrationDeliveryAttempt.integration_account_id == account.id,
            OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == action_id,
        )
        if attempt_status:
            statement = statement.where(OutboundIntegrationDeliveryAttempt.status == attempt_status)
        if started_after:
            statement = statement.where(OutboundIntegrationDeliveryAttempt.started_at >= started_after)
        if started_before:
            statement = statement.where(OutboundIntegrationDeliveryAttempt.started_at <= started_before)
        ordering = (
            OutboundIntegrationDeliveryAttempt.attempt_number.desc()
            if newest_first
            else OutboundIntegrationDeliveryAttempt.attempt_number
        )
        return list(
            self.session.exec(statement.order_by(ordering).limit(limit)).all()
        )

    def _get_action_for_account(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        action_id: UUID,
    ) -> OutboundIntegrationAction:
        action = self.session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.id == action_id,
                OutboundIntegrationAction.workspace_id == workspace.id,
                OutboundIntegrationAction.integration_account_id == account.id,
            )
        ).first()
        if not action:
            raise OutboundIntegrationActionNotFoundError("Outbound integration action not found")
        return action

    def _deliver_action(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> tuple[OutboundIntegrationAction, IntegrationAccount]:
        attempt = self._new_attempt(action)
        self.audit_service.record(action, OutboundIntegrationAuditAction.DELIVERY_ATTEMPTED)

        adapter = self.adapter_registry.get(account.provider)
        if not adapter:
            result = DeliveryAdapterResult.failure(
                "adapter_not_configured",
                "No delivery adapter is configured for this provider",
            )
        else:
            result = self._validate_adapter_capabilities(action, account)
            if result is None:
                try:
                    result = adapter.deliver(action, account)
                except Exception:  # noqa: BLE001 - adapters are an external extension boundary.
                    result = DeliveryAdapterResult.failure(
                        "adapter_execution_failed",
                        "Delivery adapter execution failed",
                    )

        self._persist_outcome(action, attempt, result)
        self.session.commit()
        self.session.refresh(action)
        return action, account

    def _validate_adapter_capabilities(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> DeliveryAdapterResult | None:
        """Return a safe failure before adapter I/O when constraints are unmet."""
        return self.adapter_registry.validate_action(account.provider, action)

    def _new_attempt(
        self,
        action: OutboundIntegrationAction,
    ) -> OutboundIntegrationDeliveryAttempt:
        previous_attempt_number = self._attempt_count(action)
        attempt = OutboundIntegrationDeliveryAttempt(
            workspace_id=action.workspace_id,
            integration_account_id=action.integration_account_id,
            outbound_integration_action_id=action.id,
            attempt_number=previous_attempt_number + 1,
            status=OutboundIntegrationActionStatus.PENDING,
            started_at=utc_now(),
        )
        self.session.add(attempt)
        return attempt

    @staticmethod
    def is_before_not_before(action: OutboundIntegrationAction) -> bool:
        if action.not_before is None:
            return False
        now = utc_now()
        if action.not_before.tzinfo is None:
            now = now.replace(tzinfo=None)
        return now < action.not_before

    @staticmethod
    def is_expired(action: OutboundIntegrationAction) -> bool:
        if action.expires_at is None:
            return False
        now = utc_now()
        if action.expires_at.tzinfo is None:
            now = now.replace(tzinfo=None)
        return action.expires_at <= now

    def _attempt_count(self, action: OutboundIntegrationAction) -> int:
        """Return the persisted attempt count without loading attempt history."""
        attempt_count = self.session.exec(
            select(func.max(OutboundIntegrationDeliveryAttempt.attempt_number)).where(
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == action.id
            )
        ).one()
        return attempt_count or 0

    def _persist_outcome(
        self,
        action: OutboundIntegrationAction,
        attempt: OutboundIntegrationDeliveryAttempt,
        result: DeliveryAdapterResult,
    ) -> None:
        recorded_at = utc_now()
        previous_status = action.status
        if result.delivered:
            self.transition_guard.require_transition(
                action, OutboundIntegrationActionStatus.DELIVERED
            )
            action.status = OutboundIntegrationActionStatus.DELIVERED
            action.provider_delivery_id = result.provider_delivery_id
            action.delivered_at = recorded_at
            action.failed_at = None
            action.failure_code = None
            action.failure_message = None
            action.failure_classification = None
            attempt.status = OutboundIntegrationActionStatus.DELIVERED
            attempt.provider_delivery_id = result.provider_delivery_id
            attempt.completed_at = recorded_at
            attempt.failure_code = None
            attempt.failure_message = None
            attempt.failure_classification = None
            self._record_transition_audit(action, previous_status)
        else:
            self.transition_guard.require_transition(
                action, OutboundIntegrationActionStatus.FAILED
            )
            action.status = OutboundIntegrationActionStatus.FAILED
            action.provider_delivery_id = None
            action.delivered_at = None
            action.failed_at = recorded_at
            action.failure_code = result.failure_code or "delivery_failed"
            action.failure_message = result.failure_message or "Delivery failed"
            action.failure_classification = result.failure_classification
            attempt.status = OutboundIntegrationActionStatus.FAILED
            attempt.provider_delivery_id = None
            attempt.completed_at = recorded_at
            attempt.failure_code = action.failure_code
            attempt.failure_message = action.failure_message
            attempt.failure_classification = action.failure_classification
            self._record_transition_audit(action, previous_status)
        self.session.add(action)
        self.session.add(attempt)

    def _record_transition_audit(
        self,
        action: OutboundIntegrationAction,
        previous_status: OutboundIntegrationActionStatus,
    ) -> None:
        """Record only audit events authorized by the shared transition guard."""
        event_action = self.transition_guard.audit_event_for_transition(
            previous_status, action.status
        )
        self.audit_service.record(action, event_action)
