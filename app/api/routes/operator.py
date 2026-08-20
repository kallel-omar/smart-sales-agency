from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import (
    CurrentWorkspaceDep,
    SalesDataReadPermissionDep,
    SessionDep,
    WorkspaceReadPermissionDep,
)
from app.core.work_items import WorkItemStatus
from app.models import ApprovalStatus
from app.schemas import (
    OperatorAIEmployeeRead,
    OperatorApprovalRead,
    OperatorWorkItemRead,
)
from app.services.operator_views import OperatorViewNotFoundError, OperatorViewService

router = APIRouter(prefix="/operator", tags=["operator"])


@router.get("/workforce", response_model=list[OperatorAIEmployeeRead])
def list_workforce(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: WorkspaceReadPermissionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[OperatorAIEmployeeRead]:
    return OperatorViewService(session).list_workforce(workspace, limit=limit)


@router.get("/workforce/{employee_id}", response_model=OperatorAIEmployeeRead)
def get_employee(
    employee_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: WorkspaceReadPermissionDep,
) -> OperatorAIEmployeeRead:
    try:
        return OperatorViewService(session).get_employee(workspace, employee_id)
    except OperatorViewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="AI employee not found") from exc


@router.get("/work-items", response_model=list[OperatorWorkItemRead])
def list_work_items(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: WorkspaceReadPermissionDep,
    status: Annotated[WorkItemStatus | None, Query()] = None,
    work_type: Annotated[str | None, Query(max_length=100)] = None,
    department_id: Annotated[UUID | None, Query()] = None,
    ai_employee_id: Annotated[UUID | None, Query()] = None,
    capability_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[OperatorWorkItemRead]:
    return OperatorViewService(session).list_work_items(
        workspace,
        status=status,
        work_type=work_type,
        department_id=department_id,
        ai_employee_id=ai_employee_id,
        capability_id=capability_id,
        limit=limit,
    )


@router.get("/work-items/{work_item_id}", response_model=OperatorWorkItemRead)
def get_work_item(
    work_item_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: WorkspaceReadPermissionDep,
) -> OperatorWorkItemRead:
    try:
        return OperatorViewService(session).get_work_item(workspace, work_item_id)
    except OperatorViewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Work item not found") from exc


@router.get("/approvals", response_model=list[OperatorApprovalRead])
def list_operator_approvals(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: SalesDataReadPermissionDep,
    status: Annotated[ApprovalStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[OperatorApprovalRead]:
    return OperatorViewService(session).list_approvals(
        workspace, status=status, limit=limit
    )


@router.get("/approvals/{approval_id}", response_model=OperatorApprovalRead)
def get_operator_approval(
    approval_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: SalesDataReadPermissionDep,
) -> OperatorApprovalRead:
    try:
        return OperatorViewService(session).get_approval(workspace, approval_id)
    except OperatorViewNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Approval not found") from exc
