"""Safe, transaction-friendly audit history for outbound action lifecycles."""

from sqlmodel import Session

from app.models import (
    OutboundIntegrationAction,
    OutboundIntegrationAuditAction,
    OutboundIntegrationAuditEvent,
)


class OutboundIntegrationActionAuditService:
    """Records safe event names only; callers commit with their transition."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        action: OutboundIntegrationAction,
        event_action: OutboundIntegrationAuditAction,
    ) -> OutboundIntegrationAuditEvent:
        event = OutboundIntegrationAuditEvent(
            workspace_id=action.workspace_id,
            integration_account_id=action.integration_account_id,
            outbound_integration_action_id=action.id,
            action=event_action,
        )
        self.session.add(event)
        return event
