"""Workspace-scoped approval checks for provider-neutral outbound actions."""

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.models import ApprovalRequest, ApprovalStatus, OutboundIntegrationAction, Workspace


class OutboundDeliveryApprovalRequiredError(ValueError):
    """Raised when a pending outbound approval has not been approved."""


class OutboundDeliveryApprovalRejectedError(ValueError):
    """Raised when the action's outbound approval was explicitly rejected."""


@dataclass(frozen=True)
class OutboundDeliveryApprovalEligibility:
    allowed: bool
    denial_reason: str | None = None


class OutboundDeliveryApprovalService:
    """Use the established ApprovalRequest model for outbound delivery gates."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_for_action(self, action: OutboundIntegrationAction, provider: str) -> ApprovalRequest:
        approval = ApprovalRequest(
            action_type=action.action_type.value,
            channel=provider,
            # The action remains the source of delivery content. Approval records
            # intentionally carry no duplicate content or provider payload.
            payload={},
        )
        self.session.add(approval)
        self.session.flush()
        action.approval_request_id = approval.id
        self.session.add(action)
        return approval

    def get_for_action(
        self,
        workspace: Workspace,
        action: OutboundIntegrationAction,
    ) -> ApprovalRequest | None:
        if action.workspace_id != workspace.id or action.approval_request_id is None:
            return None
        return self.session.get(ApprovalRequest, action.approval_request_id)

    def require_approved(
        self,
        workspace: Workspace,
        action: OutboundIntegrationAction,
    ) -> None:
        eligibility = self.evaluate(workspace, action)
        if eligibility.allowed:
            return
        if eligibility.denial_reason == "approval_rejected":
            raise OutboundDeliveryApprovalRejectedError(
                "Outbound integration action approval was rejected"
            )
        raise OutboundDeliveryApprovalRequiredError(
            "Outbound integration action requires approval before delivery"
        )

    def evaluate(
        self, workspace: Workspace, action: OutboundIntegrationAction
    ) -> OutboundDeliveryApprovalEligibility:
        """Return a safe gate decision without modifying an approval record."""
        if not action.requires_approval:
            return OutboundDeliveryApprovalEligibility(allowed=True)
        approval = self.get_for_action(workspace, action)
        if approval is None:
            return OutboundDeliveryApprovalEligibility(False, "approval_unavailable")
        if approval.status == ApprovalStatus.PENDING:
            return OutboundDeliveryApprovalEligibility(False, "approval_pending")
        if approval.status == ApprovalStatus.REJECTED:
            return OutboundDeliveryApprovalEligibility(False, "approval_rejected")
        if approval.status != ApprovalStatus.APPROVED:
            return OutboundDeliveryApprovalEligibility(False, "approval_not_approved")
        return OutboundDeliveryApprovalEligibility(allowed=True)

    def get_scoped_approval(
        self, workspace: Workspace, approval_id: UUID
    ) -> ApprovalRequest | None:
        return self.session.exec(
            select(ApprovalRequest)
            .join(
                OutboundIntegrationAction,
                OutboundIntegrationAction.approval_request_id == ApprovalRequest.id,
            )
            .where(
                ApprovalRequest.id == approval_id,
                OutboundIntegrationAction.approval_request_id == ApprovalRequest.id,
                OutboundIntegrationAction.workspace_id == workspace.id,
            )
        ).first()
