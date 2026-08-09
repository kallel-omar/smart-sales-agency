"""Safe, transaction-friendly audit history for outbound action lifecycles."""

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    OutboundIntegrationAction,
    OutboundIntegrationAuditAction,
    OutboundIntegrationAuditEvent,
    Workspace,
)

DEFAULT_OUTBOUND_AUDIT_EVENT_LIMIT = 50
MAX_OUTBOUND_AUDIT_EVENT_LIMIT = 100


class OutboundAuditQueryValidationError(ValueError):
    """Raised when an outbound audit query is outside safe supported bounds."""


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

    def list_for_workspace(
        self,
        workspace: Workspace,
        *,
        action: OutboundIntegrationAuditAction | None = None,
        integration_account_id: UUID | None = None,
        outbound_integration_action_id: UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = DEFAULT_OUTBOUND_AUDIT_EVENT_LIMIT,
    ) -> list[OutboundIntegrationAuditEvent]:
        """Return safe lifecycle history from the resolved workspace only."""
        self._validate_query(created_after, created_before, limit)

        filters = [OutboundIntegrationAuditEvent.workspace_id == workspace.id]
        if action is not None:
            filters.append(OutboundIntegrationAuditEvent.action == action)
        if integration_account_id is not None:
            filters.append(
                OutboundIntegrationAuditEvent.integration_account_id == integration_account_id
            )
        if outbound_integration_action_id is not None:
            filters.append(
                OutboundIntegrationAuditEvent.outbound_integration_action_id
                == outbound_integration_action_id
            )
        if created_after is not None:
            filters.append(OutboundIntegrationAuditEvent.created_at >= created_after)
        if created_before is not None:
            filters.append(OutboundIntegrationAuditEvent.created_at <= created_before)

        statement = (
            select(OutboundIntegrationAuditEvent)
            .where(*filters)
            .order_by(
                OutboundIntegrationAuditEvent.created_at.desc(),
                OutboundIntegrationAuditEvent.id.desc(),
            )
            .limit(limit)
        )
        return list(self.session.exec(statement).all())

    @staticmethod
    def _validate_query(
        created_after: datetime | None,
        created_before: datetime | None,
        limit: int,
    ) -> None:
        if not 1 <= limit <= MAX_OUTBOUND_AUDIT_EVENT_LIMIT:
            raise OutboundAuditQueryValidationError(
                "Outbound audit limit must be between 1 and "
                f"{MAX_OUTBOUND_AUDIT_EVENT_LIMIT}"
            )
        if (
            created_after is not None
            and created_before is not None
            and created_after > created_before
        ):
            raise OutboundAuditQueryValidationError(
                "created_after must not be later than created_before"
            )
