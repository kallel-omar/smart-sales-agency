"""Workspace-scoped human approval decisions with durable actor attribution."""

from dataclasses import dataclass
from datetime import timezone
from uuid import UUID

from sqlmodel import Session, select

from app.channels.console import ConsoleChannel
from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    Lead,
    SalesStage,
    Workspace,
    WorkspaceMemberRole,
    utc_now,
)
from app.services.outbound_delivery_approvals import OutboundDeliveryApprovalService


class ApprovalDecisionNotFoundError(LookupError):
    """Safe not-found result for approvals outside the selected workspace."""


class ApprovalDecisionConflictError(ValueError):
    """Raised when an approval cannot leave its current lifecycle state."""


class ApprovalDecisionDeliveryError(RuntimeError):
    """Raised when the existing approval side effect cannot complete."""


class ApprovalDecisionActorWorkspaceMismatchError(PermissionError):
    """Raised when a trusted actor context does not match the approval workspace."""


@dataclass(frozen=True)
class ApprovalDecisionActor:
    """Trusted human actor snapshot derived from authenticated workspace context."""

    user_id: UUID
    membership_id: UUID
    workspace_id: UUID
    role: WorkspaceMemberRole


class ApprovalDecisionService:
    """Canonical approval/rejection lifecycle boundary for human decisions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    async def approve(
        self,
        *,
        workspace: Workspace,
        approval_id: UUID,
        reviewer_note: str | None,
        actor: ApprovalDecisionActor,
    ) -> ApprovalRequest:
        approval = self._pending_scoped_approval(workspace, approval_id, actor)

        if approval.lead_id is None:
            self._record_decision(
                approval,
                status=ApprovalStatus.APPROVED,
                reviewer_note=reviewer_note,
                actor=actor,
            )
            return self._commit_and_refresh(approval)

        delivery = await ConsoleChannel().send(
            recipient=str(approval.payload.get("recipient", "unknown")),
            content=str(approval.payload.get("content", "")),
        )
        if not delivery.success:
            raise ApprovalDecisionDeliveryError(delivery.error or "Delivery failed")

        self._record_decision(
            approval,
            status=ApprovalStatus.EXECUTED,
            reviewer_note=reviewer_note,
            actor=actor,
        )
        self.session.add(
            ConversationMessage(
                lead_id=approval.lead_id,
                direction="outbound",
                channel=approval.channel,
                stage=self._message_stage(approval),
                content=str(approval.payload.get("content", "")),
            )
        )
        return self._commit_and_refresh(approval)

    def reject(
        self,
        *,
        workspace: Workspace,
        approval_id: UUID,
        reviewer_note: str | None,
        actor: ApprovalDecisionActor,
    ) -> ApprovalRequest:
        approval = self._pending_scoped_approval(workspace, approval_id, actor)
        self._record_decision(
            approval,
            status=ApprovalStatus.REJECTED,
            reviewer_note=reviewer_note,
            actor=actor,
        )
        return self._commit_and_refresh(approval)

    def get_scoped_approval(
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

        approval = OutboundDeliveryApprovalService(self.session).get_scoped_approval(
            workspace,
            approval_id,
        )
        if approval is None:
            raise ApprovalDecisionNotFoundError("Approval request not found")
        return approval

    def _pending_scoped_approval(
        self,
        workspace: Workspace,
        approval_id: UUID,
        actor: ApprovalDecisionActor,
    ) -> ApprovalRequest:
        if actor.workspace_id != workspace.id:
            raise ApprovalDecisionActorWorkspaceMismatchError(
                "Approval actor does not belong to this workspace"
            )

        approval = self.get_scoped_approval(workspace, approval_id)
        if approval.status != ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError("Approval request is already decided")
        return approval

    @staticmethod
    def _record_decision(
        approval: ApprovalRequest,
        *,
        status: ApprovalStatus,
        reviewer_note: str | None,
        actor: ApprovalDecisionActor,
    ) -> None:
        approval.status = status
        approval.reviewer_note = reviewer_note
        approval.decided_at = utc_now().astimezone(timezone.utc)
        approval.decided_by_user_id = actor.user_id
        approval.decided_by_membership_id = actor.membership_id
        approval.decided_by_role = actor.role

    def _commit_and_refresh(self, approval: ApprovalRequest) -> ApprovalRequest:
        self.session.add(approval)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(approval)
        return approval

    @staticmethod
    def _message_stage(approval: ApprovalRequest) -> SalesStage:
        stage_value = str(approval.payload.get("stage", SalesStage.FOLLOW_UP.value))
        try:
            return SalesStage(stage_value)
        except ValueError:
            return SalesStage.FOLLOW_UP
