"""Explicit orchestration for provider-neutral outbound action delivery."""

from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
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

        action = self.session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.id == action_id,
                OutboundIntegrationAction.workspace_id == workspace.id,
                OutboundIntegrationAction.integration_account_id == account.id,
            )
        ).first()
        if not action:
            raise OutboundIntegrationActionNotFoundError("Outbound integration action not found")
        if action.status != OutboundIntegrationActionStatus.PENDING:
            raise OutboundIntegrationActionAlreadyProcessedError(
                "Outbound integration action has already reached a terminal state"
            )

        adapter = self.adapter_registry.get(account.provider)
        if not adapter:
            result = DeliveryAdapterResult.failure(
                "adapter_not_configured",
                "No delivery adapter is configured for this provider",
            )
        else:
            result = adapter.deliver(action, account)

        self._persist_outcome(action, result)
        self.session.commit()
        self.session.refresh(action)
        return action, account

    def _persist_outcome(
        self,
        action: OutboundIntegrationAction,
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
        else:
            action.status = OutboundIntegrationActionStatus.FAILED
            action.provider_delivery_id = None
            action.delivered_at = None
            action.failed_at = recorded_at
            action.failure_code = result.failure_code or "delivery_failed"
            action.failure_message = result.failure_message or "Delivery failed"
        self.session.add(action)
