"""Non-destructive workspace-scoped archiving for terminal outbound actions."""

from uuid import UUID

from sqlmodel import Session

from app.models import OutboundIntegrationAction, OutboundIntegrationAuditAction, Workspace, utc_now
from app.services.outbound_action_audit import OutboundIntegrationActionAuditService
from app.services.outbound_action_query import OutboundIntegrationActionQueryService
from app.services.outbound_action_state_transitions import OutboundIntegrationActionStateTransitionGuard


class OutboundIntegrationActionNotArchivableError(ValueError):
    """Raised when an action still has available delivery lifecycle transitions."""


class OutboundIntegrationActionAlreadyArchivedError(ValueError):
    """Raised when an action is archived more than once without unarchiving."""


class OutboundIntegrationActionNotArchivedError(ValueError):
    """Raised when an action is unarchived without a current archive state."""


class OutboundActionArchivingService:
    """Archive terminal actions without changing delivery, approval, or attempt state."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.action_query = OutboundIntegrationActionQueryService(session)
        self.audit_service = OutboundIntegrationActionAuditService(session)
        self.transition_guard = OutboundIntegrationActionStateTransitionGuard()

    def archive(self, workspace: Workspace, action_id: UUID) -> OutboundIntegrationAction:
        action, _ = self.action_query.get_for_workspace(workspace, action_id)
        if action.archived_at is not None:
            raise OutboundIntegrationActionAlreadyArchivedError(
                "Outbound integration action is already archived"
            )
        if not self.transition_guard.is_terminal(action):
            raise OutboundIntegrationActionNotArchivableError(
                "Only terminal outbound integration actions can be archived"
            )
        action.archived_at = utc_now()
        self.session.add(action)
        self.audit_service.record(action, OutboundIntegrationAuditAction.ARCHIVED)
        self.session.commit()
        self.session.refresh(action)
        return action

    def unarchive(self, workspace: Workspace, action_id: UUID) -> OutboundIntegrationAction:
        action, _ = self.action_query.get_for_workspace(workspace, action_id)
        if action.archived_at is None:
            raise OutboundIntegrationActionNotArchivedError(
                "Outbound integration action is not archived"
            )
        action.archived_at = None
        self.session.add(action)
        self.audit_service.record(action, OutboundIntegrationAuditAction.UNARCHIVED)
        self.session.commit()
        self.session.refresh(action)
        return action
