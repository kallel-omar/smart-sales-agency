"""Read-only approval status views for workspace-scoped outbound actions."""

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.models import ApprovalRequest, ApprovalStatus, IntegrationAccount, OutboundIntegrationAction, Workspace
from app.services.integration_accounts import IntegrationAccountService


class OutboundApprovalStatusNotFoundError(LookupError):
    """Raised when an action is absent from the requesting account/workspace."""


@dataclass(frozen=True)
class OutboundApprovalStatusView:
    action_id: UUID
    requires_approval: bool
    approval_request_id: UUID | None
    approval_status: ApprovalStatus | None


class OutboundApprovalStatusService:
    """Loads safe approval state without changing approval or delivery data."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.account_service = IntegrationAccountService(session)

    def get_for_action(
        self, workspace: Workspace, account_id: UUID, action_id: UUID
    ) -> OutboundApprovalStatusView:
        account = self.account_service.get_for_workspace(workspace, account_id)
        action = self._get_action(workspace, account, action_id)
        approval = (
            self.session.get(ApprovalRequest, action.approval_request_id)
            if action.approval_request_id is not None
            else None
        )
        return OutboundApprovalStatusView(
            action_id=action.id,
            requires_approval=action.requires_approval,
            approval_request_id=action.approval_request_id,
            approval_status=approval.status if approval else None,
        )

    def _get_action(
        self, workspace: Workspace, account: IntegrationAccount, action_id: UUID
    ) -> OutboundIntegrationAction:
        action = self.session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.id == action_id,
                OutboundIntegrationAction.workspace_id == workspace.id,
                OutboundIntegrationAction.integration_account_id == account.id,
            )
        ).first()
        if action is None:
            raise OutboundApprovalStatusNotFoundError("Outbound integration action not found")
        return action
