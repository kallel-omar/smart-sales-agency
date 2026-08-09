"""Read-only state-transition history derived from safe outbound audit events."""

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session

from app.models import (
    OutboundIntegrationActionStatus,
    OutboundIntegrationAuditAction,
    Workspace,
)
from app.services.integration_accounts import IntegrationAccountService
from app.services.outbound_action_audit import OutboundIntegrationActionAuditService
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


DEFAULT_OUTBOUND_STATE_HISTORY_LIMIT = 50
MAX_OUTBOUND_STATE_HISTORY_LIMIT = 100


@dataclass(frozen=True)
class OutboundActionStateHistoryEntry:
    """Safe representation of one persisted successful state transition."""

    state: OutboundIntegrationActionStatus
    event: OutboundIntegrationAuditAction
    created_at: object


class OutboundActionStateHistoryService:
    """Expose transition history without adding a parallel persistence record."""

    _STATE_BY_EVENT = {
        OutboundIntegrationAuditAction.DELIVERED: OutboundIntegrationActionStatus.DELIVERED,
        OutboundIntegrationAuditAction.FAILED: OutboundIntegrationActionStatus.FAILED,
        OutboundIntegrationAuditAction.CANCELLED: OutboundIntegrationActionStatus.CANCELLED,
        OutboundIntegrationAuditAction.EXPIRED: OutboundIntegrationActionStatus.EXPIRED,
    }

    def __init__(self, session: Session) -> None:
        self.session = session
        self.account_service = IntegrationAccountService(session)
        self.delivery_service = OutboundIntegrationDeliveryService(session)
        self.audit_service = OutboundIntegrationActionAuditService(session)

    def list_for_action(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
        *,
        limit: int = DEFAULT_OUTBOUND_STATE_HISTORY_LIMIT,
    ) -> list[OutboundActionStateHistoryEntry]:
        if not 1 <= limit <= MAX_OUTBOUND_STATE_HISTORY_LIMIT:
            raise ValueError(
                "Outbound state history limit must be between 1 and "
                f"{MAX_OUTBOUND_STATE_HISTORY_LIMIT}"
            )
        account = self.account_service.get_for_workspace(workspace, account_id)
        self.delivery_service._get_action_for_account(workspace, account, action_id)
        events = self.audit_service.list_for_workspace(
            workspace,
            integration_account_id=account.id,
            outbound_integration_action_id=action_id,
            limit=MAX_OUTBOUND_STATE_HISTORY_LIMIT,
        )
        transitions = [
            OutboundActionStateHistoryEntry(
                state=self._STATE_BY_EVENT[event.action],
                event=event.action,
                created_at=event.created_at,
            )
            for event in events
            if event.action in self._STATE_BY_EVENT
        ]
        return list(reversed(transitions))[-limit:]
