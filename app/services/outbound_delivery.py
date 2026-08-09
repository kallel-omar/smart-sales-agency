"""Explicit orchestration for provider-neutral outbound action delivery."""

from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationDeliveryAttempt,
    Workspace,
    utc_now,
)
from app.services.delivery_adapters import (
    DeliveryAdapterRegistry,
    DeliveryAdapterResult,
    default_delivery_adapter_registry,
)
from app.services.integration_accounts import IntegrationAccountService
from app.services.outbound_integrations import InactiveIntegrationAccountError


class OutboundIntegrationActionNotFoundError(LookupError):
    """Raised when an action is absent from the requesting account/workspace."""


class OutboundIntegrationActionAlreadyProcessedError(ValueError):
    """Raised when a terminal action is explicitly submitted for delivery again."""


class OutboundIntegrationActionNotRetryableError(ValueError):
    """Raised when an action is not a failed action eligible for explicit retry."""


class OutboundIntegrationDeliveryService:
    """Delivers one pending action and persists only its safe outcome."""

    def __init__(
        self,
        session: Session,
        adapter_registry: DeliveryAdapterRegistry | None = None,
    ) -> None:
        self.session = session
        self.account_service = IntegrationAccountService(session)
        self.adapter_registry = adapter_registry or default_delivery_adapter_registry()

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
        if action.status != OutboundIntegrationActionStatus.PENDING:
            raise OutboundIntegrationActionAlreadyProcessedError(
                "Outbound integration action has already reached a terminal state"
            )

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
        if action.status != OutboundIntegrationActionStatus.FAILED:
            raise OutboundIntegrationActionNotRetryableError(
                "Only failed outbound integration actions can be retried"
            )

        return self._deliver_action(action, account)

    def list_attempts_for_action(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
    ) -> list[OutboundIntegrationDeliveryAttempt]:
        """Return safe attempt history only after workspace/account/action scoping."""
        account = self.account_service.get_for_workspace(workspace, account_id)
        self._get_action_for_account(workspace, account, action_id)
        return list(
            self.session.exec(
                select(OutboundIntegrationDeliveryAttempt)
                .where(
                    OutboundIntegrationDeliveryAttempt.workspace_id == workspace.id,
                    OutboundIntegrationDeliveryAttempt.integration_account_id == account.id,
                    OutboundIntegrationDeliveryAttempt.outbound_integration_action_id
                    == action_id,
                )
                .order_by(OutboundIntegrationDeliveryAttempt.attempt_number)
            ).all()
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

        adapter = self.adapter_registry.get(account.provider)
        if not adapter:
            result = DeliveryAdapterResult.failure(
                "adapter_not_configured",
                "No delivery adapter is configured for this provider",
            )
        else:
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

    def _new_attempt(
        self,
        action: OutboundIntegrationAction,
    ) -> OutboundIntegrationDeliveryAttempt:
        previous_attempt_number = self.session.exec(
            select(func.max(OutboundIntegrationDeliveryAttempt.attempt_number)).where(
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == action.id
            )
        ).one()
        attempt = OutboundIntegrationDeliveryAttempt(
            workspace_id=action.workspace_id,
            integration_account_id=action.integration_account_id,
            outbound_integration_action_id=action.id,
            attempt_number=(previous_attempt_number or 0) + 1,
            status=OutboundIntegrationActionStatus.PENDING,
            started_at=utc_now(),
        )
        self.session.add(attempt)
        return attempt

    def _persist_outcome(
        self,
        action: OutboundIntegrationAction,
        attempt: OutboundIntegrationDeliveryAttempt,
        result: DeliveryAdapterResult,
    ) -> None:
        recorded_at = utc_now()
        if result.delivered:
            action.status = OutboundIntegrationActionStatus.DELIVERED
            action.provider_delivery_id = result.provider_delivery_id
            action.delivered_at = recorded_at
            action.failed_at = None
            action.failure_code = None
            action.failure_message = None
            attempt.status = OutboundIntegrationActionStatus.DELIVERED
            attempt.provider_delivery_id = result.provider_delivery_id
            attempt.completed_at = recorded_at
            attempt.failure_code = None
            attempt.failure_message = None
        else:
            action.status = OutboundIntegrationActionStatus.FAILED
            action.provider_delivery_id = None
            action.delivered_at = None
            action.failed_at = recorded_at
            action.failure_code = result.failure_code or "delivery_failed"
            action.failure_message = result.failure_message or "Delivery failed"
            attempt.status = OutboundIntegrationActionStatus.FAILED
            attempt.provider_delivery_id = None
            attempt.completed_at = recorded_at
            attempt.failure_code = action.failure_code
            attempt.failure_message = action.failure_message
        self.session.add(action)
        self.session.add(attempt)
