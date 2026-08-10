"""Workspace-scoped human responsibility assignment for Sales work."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    Lead,
    OutboundIntegrationAction,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
    utc_now,
)


class OperatorAssignmentNotFoundError(LookupError):
    """Safe not-found result for resources or targets outside the workspace."""


class OperatorAssignmentConflictError(ValueError):
    """Raised when the resource lifecycle does not allow assignment mutation."""


class OperatorAssignmentActorWorkspaceMismatchError(PermissionError):
    """Raised when the trusted actor is not scoped to the selected workspace."""


@dataclass(frozen=True)
class OperatorAssignmentActor:
    """Trusted assignment actor derived from authenticated workspace context."""

    user_id: UUID
    membership_id: UUID
    workspace_id: UUID
    role: WorkspaceMemberRole


@dataclass(frozen=True)
class OperatorAssignmentSnapshot:
    assigned_to_membership_id: UUID
    assigned_to_user_id: UUID | None
    assigned_to_display_name: str | None
    assigned_at: datetime | None
    assigned_by_user_id: UUID | None
    assigned_by_membership_id: UUID | None
    assignee_membership_active: bool | None
    assignee_user_active: bool | None


class OperatorAssignmentService:
    """Current-assignee operations for workspace-scoped human work items."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def assign_lead(
        self,
        *,
        workspace: Workspace,
        lead_id: UUID,
        target_membership_id: UUID,
        actor: OperatorAssignmentActor,
    ) -> Lead:
        self._ensure_actor_workspace(workspace, actor)
        lead = self._get_scoped_lead(workspace, lead_id)
        target = self._eligible_target_membership(workspace, target_membership_id)
        self._set_assignment(lead, target, actor)
        return self._commit_and_refresh(lead)

    def clear_lead(
        self,
        *,
        workspace: Workspace,
        lead_id: UUID,
        actor: OperatorAssignmentActor,
    ) -> Lead:
        self._ensure_actor_workspace(workspace, actor)
        lead = self._get_scoped_lead(workspace, lead_id)
        self._clear_assignment(lead)
        return self._commit_and_refresh(lead)

    def assign_approval(
        self,
        *,
        workspace: Workspace,
        approval_id: UUID,
        target_membership_id: UUID,
        actor: OperatorAssignmentActor,
    ) -> ApprovalRequest:
        self._ensure_actor_workspace(workspace, actor)
        approval = self._get_scoped_approval(workspace, approval_id)
        self._ensure_pending_approval(approval)
        target = self._eligible_target_membership(workspace, target_membership_id)
        self._set_assignment(approval, target, actor)
        return self._commit_and_refresh(approval)

    def clear_approval(
        self,
        *,
        workspace: Workspace,
        approval_id: UUID,
        actor: OperatorAssignmentActor,
    ) -> ApprovalRequest:
        self._ensure_actor_workspace(workspace, actor)
        approval = self._get_scoped_approval(workspace, approval_id)
        self._ensure_pending_approval(approval)
        self._clear_assignment(approval)
        return self._commit_and_refresh(approval)

    def resolve_lead_assignment(self, lead: Lead) -> OperatorAssignmentSnapshot | None:
        return self._resolve_assignment(
            assigned_to_membership_id=lead.assigned_to_membership_id,
            assigned_at=lead.assigned_at,
            assigned_by_user_id=lead.assigned_by_user_id,
            assigned_by_membership_id=lead.assigned_by_membership_id,
        )

    def resolve_approval_assignment(
        self,
        approval: ApprovalRequest,
    ) -> OperatorAssignmentSnapshot | None:
        return self._resolve_assignment(
            assigned_to_membership_id=approval.assigned_to_membership_id,
            assigned_at=approval.assigned_at,
            assigned_by_user_id=approval.assigned_by_user_id,
            assigned_by_membership_id=approval.assigned_by_membership_id,
        )

    def _get_scoped_lead(self, workspace: Workspace, lead_id: UUID) -> Lead:
        lead = self.session.get(Lead, lead_id)
        if lead is None or lead.tenant_id != workspace.slug:
            raise OperatorAssignmentNotFoundError("Lead not found")
        return lead

    def _get_scoped_approval(
        self,
        workspace: Workspace,
        approval_id: UUID,
    ) -> ApprovalRequest:
        lead_approval = self.session.exec(
            select(ApprovalRequest)
            .join(Lead, ApprovalRequest.lead_id == Lead.id)
            .where(
                ApprovalRequest.id == approval_id,
                Lead.tenant_id == workspace.slug,
            )
        ).first()
        if lead_approval is not None:
            return lead_approval

        outbound_approval = self.session.exec(
            select(ApprovalRequest)
            .join(
                OutboundIntegrationAction,
                OutboundIntegrationAction.approval_request_id == ApprovalRequest.id,
            )
            .where(
                ApprovalRequest.id == approval_id,
                OutboundIntegrationAction.workspace_id == workspace.id,
            )
        ).first()
        if outbound_approval is None:
            raise OperatorAssignmentNotFoundError("Approval request not found")
        return outbound_approval

    def _eligible_target_membership(
        self,
        workspace: Workspace,
        target_membership_id: UUID,
    ) -> WorkspaceMember:
        membership = self.session.get(WorkspaceMember, target_membership_id)
        if (
            membership is None
            or membership.workspace_id != workspace.id
            or not membership.active
        ):
            raise OperatorAssignmentNotFoundError("Workspace member not found")

        user = self.session.get(User, membership.user_id)
        if user is None or not user.active:
            raise OperatorAssignmentNotFoundError("Workspace member not found")
        return membership

    @staticmethod
    def _ensure_actor_workspace(
        workspace: Workspace,
        actor: OperatorAssignmentActor,
    ) -> None:
        if actor.workspace_id != workspace.id:
            raise OperatorAssignmentActorWorkspaceMismatchError(
                "Assignment actor does not belong to this workspace"
            )

    @staticmethod
    def _ensure_pending_approval(approval: ApprovalRequest) -> None:
        if approval.status != ApprovalStatus.PENDING:
            raise OperatorAssignmentConflictError(
                "Approval assignment can only be changed while pending"
            )

    @staticmethod
    def _set_assignment(
        resource,
        target: WorkspaceMember,
        actor: OperatorAssignmentActor,
    ) -> None:
        resource.assigned_to_membership_id = target.id
        resource.assigned_at = utc_now()
        resource.assigned_by_user_id = actor.user_id
        resource.assigned_by_membership_id = actor.membership_id

    @staticmethod
    def _clear_assignment(resource) -> None:
        resource.assigned_to_membership_id = None
        resource.assigned_at = None
        resource.assigned_by_user_id = None
        resource.assigned_by_membership_id = None

    def _resolve_assignment(
        self,
        *,
        assigned_to_membership_id: UUID | None,
        assigned_at: datetime | None,
        assigned_by_user_id: UUID | None,
        assigned_by_membership_id: UUID | None,
    ) -> OperatorAssignmentSnapshot | None:
        if assigned_to_membership_id is None:
            return None

        membership = self.session.get(WorkspaceMember, assigned_to_membership_id)
        user = self.session.get(User, membership.user_id) if membership is not None else None
        return OperatorAssignmentSnapshot(
            assigned_to_membership_id=assigned_to_membership_id,
            assigned_to_user_id=user.id if user is not None else None,
            assigned_to_display_name=user.display_name if user is not None else None,
            assigned_at=assigned_at,
            assigned_by_user_id=assigned_by_user_id,
            assigned_by_membership_id=assigned_by_membership_id,
            assignee_membership_active=membership.active if membership is not None else None,
            assignee_user_active=user.active if user is not None else None,
        )

    def _commit_and_refresh(self, resource):
        self.session.add(resource)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(resource)
        return resource
