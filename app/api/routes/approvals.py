from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from app.api.dependencies import CurrentWorkspaceDep, SessionDep
from app.channels.console import ConsoleChannel
from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    Lead,
    OutboundIntegrationAction,
    SalesStage,
    Workspace,
)
from app.services.outbound_delivery_approvals import OutboundDeliveryApprovalService
from app.schemas import ApprovalDecision, ApprovalRead
from app.services.repository import SalesRepository

router = APIRouter(prefix="/approvals", tags=["approvals"])


def get_workspace_approval(
    approval_id: UUID,
    session: SessionDep,
    workspace: Workspace,
) -> ApprovalRequest:
    lead_approval = session.exec(
        select(ApprovalRequest)
        .join(Lead, ApprovalRequest.lead_id == Lead.id)
        .where(
            ApprovalRequest.id == approval_id,
            Lead.tenant_id == workspace.slug,
        )
    ).first()

    if lead_approval:
        return lead_approval

    approval = OutboundDeliveryApprovalService(session).get_scoped_approval(
        workspace,
        approval_id,
    )
    if not approval:
        raise HTTPException(
            status_code=404,
            detail="Approval request not found",
        )

    return approval


@router.get("", response_model=list[ApprovalRead])
def list_approvals(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    status: ApprovalStatus | None = Query(default=None),
) -> list[ApprovalRequest]:
    return SalesRepository(session).list_approvals(
        workspace.slug,
        status,
    )


@router.post("/{approval_id}/approve", response_model=ApprovalRead)
async def approve_action(
    approval_id: UUID,
    payload: ApprovalDecision,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> ApprovalRequest:
    approval = get_workspace_approval(
        approval_id,
        session,
        workspace,
    )

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail="Approval request is already decided",
        )

    approval.status = ApprovalStatus.APPROVED
    approval.reviewer_note = payload.reviewer_note
    approval.decided_at = datetime.now(timezone.utc)

    session.add(approval)
    session.commit()

    if approval.lead_id is None:
        session.refresh(approval)
        return approval

    # Safe demo channel. Later, this will select WhatsApp, email, etc.
    delivery = await ConsoleChannel().send(
        recipient=str(approval.payload.get("recipient", "unknown")),
        content=str(approval.payload.get("content", "")),
    )

    if not delivery.success:
        raise HTTPException(
            status_code=502,
            detail=delivery.error or "Delivery failed",
        )

    approval.status = ApprovalStatus.EXECUTED

    stage_value = str(
        approval.payload.get(
            "stage",
            SalesStage.FOLLOW_UP.value,
        )
    )

    try:
        message_stage = SalesStage(stage_value)
    except ValueError:
        message_stage = SalesStage.FOLLOW_UP

    session.add(
        ConversationMessage(
            lead_id=approval.lead_id,
            direction="outbound",
            channel=approval.channel,
            stage=message_stage,
            content=str(approval.payload.get("content", "")),
        )
    )

    session.add(approval)
    session.commit()
    session.refresh(approval)

    return approval


@router.post("/{approval_id}/reject", response_model=ApprovalRead)
def reject_action(
    approval_id: UUID,
    payload: ApprovalDecision,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> ApprovalRequest:
    approval = get_workspace_approval(
        approval_id,
        session,
        workspace,
    )
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail="Approval request is already decided")

    approval.status = ApprovalStatus.REJECTED
    approval.reviewer_note = payload.reviewer_note
    approval.decided_at = datetime.now(timezone.utc)
    session.add(approval)
    session.commit()
    session.refresh(approval)
    return approval
