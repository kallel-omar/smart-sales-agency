from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import Session

from app.api.dependencies import (
    ApprovalDecidePermissionDep,
    ApprovalDecisionActorDep,
    CurrentWorkspaceDep,
    OperatorAssignmentActorDep,
    OperatorAssignmentManagePermissionDep,
    SalesDataReadPermissionDep,
    SessionDep,
)
from app.models import (
    ApprovalRequest,
    ApprovalStatus,
)
from app.schemas import (
    ApprovalDecision,
    ApprovalRead,
    OperatorAssignmentRead,
    OperatorAssignmentUpdate,
)
from app.services.approval_decisions import (
    ApprovalDecisionActorWorkspaceMismatchError,
    ApprovalDecisionConflictError,
    ApprovalDecisionDeliveryError,
    ApprovalDecisionNotFoundError,
    ApprovalDecisionService,
)
from app.services.operator_assignments import (
    OperatorAssignmentActorWorkspaceMismatchError,
    OperatorAssignmentConflictError,
    OperatorAssignmentNotFoundError,
    OperatorAssignmentService,
)
from app.services.repository import SalesRepository

router = APIRouter(prefix="/approvals", tags=["approvals"])


def approval_read(session: Session, approval: ApprovalRequest) -> ApprovalRead:
    snapshot = OperatorAssignmentService(session).resolve_approval_assignment(approval)
    assignment = OperatorAssignmentRead(**snapshot.__dict__) if snapshot is not None else None
    return ApprovalRead.model_validate(approval).model_copy(update={"assignment": assignment})


@router.get("", response_model=list[ApprovalRead])
def list_approvals(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: SalesDataReadPermissionDep,
    status: ApprovalStatus | None = Query(default=None),
) -> list[ApprovalRead]:
    approvals = SalesRepository(session).list_approvals(
        workspace.slug,
        status,
    )
    return [approval_read(session, approval) for approval in approvals]


@router.put("/{approval_id}/assignment", response_model=ApprovalRead)
def assign_approval_operator(
    approval_id: UUID,
    payload: OperatorAssignmentUpdate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OperatorAssignmentManagePermissionDep,
    actor: OperatorAssignmentActorDep,
) -> ApprovalRead:
    try:
        approval = OperatorAssignmentService(session).assign_approval(
            workspace=workspace,
            approval_id=approval_id,
            target_membership_id=payload.workspace_member_id,
            actor=actor,
        )
    except (
        OperatorAssignmentNotFoundError,
        OperatorAssignmentActorWorkspaceMismatchError,
    ) as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc
    except OperatorAssignmentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return approval_read(session, approval)


@router.delete("/{approval_id}/assignment", response_model=ApprovalRead)
def clear_approval_operator(
    approval_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OperatorAssignmentManagePermissionDep,
    actor: OperatorAssignmentActorDep,
) -> ApprovalRead:
    try:
        approval = OperatorAssignmentService(session).clear_approval(
            workspace=workspace,
            approval_id=approval_id,
            actor=actor,
        )
    except (
        OperatorAssignmentNotFoundError,
        OperatorAssignmentActorWorkspaceMismatchError,
    ) as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc
    except OperatorAssignmentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return approval_read(session, approval)


@router.post("/{approval_id}/approve", response_model=ApprovalRead)
async def approve_action(
    approval_id: UUID,
    payload: ApprovalDecision,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: ApprovalDecidePermissionDep,
    actor: ApprovalDecisionActorDep,
) -> ApprovalRead:
    try:
        approval = await ApprovalDecisionService(session).approve(
            workspace=workspace,
            approval_id=approval_id,
            reviewer_note=payload.reviewer_note,
            actor=actor,
        )
    except ApprovalDecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc
    except ApprovalDecisionActorWorkspaceMismatchError as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc
    except ApprovalDecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApprovalDecisionDeliveryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return approval_read(session, approval)


@router.post("/{approval_id}/reject", response_model=ApprovalRead)
def reject_action(
    approval_id: UUID,
    payload: ApprovalDecision,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: ApprovalDecidePermissionDep,
    actor: ApprovalDecisionActorDep,
) -> ApprovalRead:
    try:
        approval = ApprovalDecisionService(session).reject(
            workspace=workspace,
            approval_id=approval_id,
            reviewer_note=payload.reviewer_note,
            actor=actor,
        )
    except ApprovalDecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc
    except ApprovalDecisionActorWorkspaceMismatchError as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc
    except ApprovalDecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return approval_read(session, approval)
