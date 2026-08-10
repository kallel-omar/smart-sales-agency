from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import (
    ApprovalDecidePermissionDep,
    ApprovalDecisionActorDep,
    CurrentWorkspaceDep,
    SalesDataReadPermissionDep,
    SessionDep,
)
from app.models import (
    ApprovalRequest,
    ApprovalStatus,
)
from app.schemas import ApprovalDecision, ApprovalRead
from app.services.approval_decisions import (
    ApprovalDecisionActorWorkspaceMismatchError,
    ApprovalDecisionConflictError,
    ApprovalDecisionDeliveryError,
    ApprovalDecisionNotFoundError,
    ApprovalDecisionService,
)
from app.services.repository import SalesRepository

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalRead])
def list_approvals(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: SalesDataReadPermissionDep,
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
    _: ApprovalDecidePermissionDep,
    actor: ApprovalDecisionActorDep,
) -> ApprovalRequest:
    try:
        return await ApprovalDecisionService(session).approve(
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


@router.post("/{approval_id}/reject", response_model=ApprovalRead)
def reject_action(
    approval_id: UUID,
    payload: ApprovalDecision,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: ApprovalDecidePermissionDep,
    actor: ApprovalDecisionActorDep,
) -> ApprovalRequest:
    try:
        return ApprovalDecisionService(session).reject(
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
