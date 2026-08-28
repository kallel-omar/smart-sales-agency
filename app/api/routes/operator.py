from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query

from app.api.dependencies import (
    ConversationOperatePermissionDep,
    CurrentWorkspaceDep,
    OperatorAssignmentActorDep,
    OutboundActionOperatePermissionDep,
    SalesDataReadPermissionDep,
    SessionDep,
    SettingsDep,
    WorkspaceReadPermissionDep,
)
from app.core.work_items import WorkItemStatus
from app.models import ApprovalStatus, OutboundIntegrationActionStatus
from app.schemas import (
    ConversationMessageRead,
    HumanHandoffReplyCreate,
    HumanHandoffReplyRead,
    OperatorAIEmployeeRead,
    OperatorAnalyticsRead,
    OperatorApprovalRead,
    OperatorAssignmentRead,
    OperatorHandoffDetailRead,
    OperatorHandoffLeadRead,
    OperatorHandoffRead,
    OperatorHandoffResolutionRead,
    OperatorWorkItemRead,
)
from app.services.human_handoff_operations import (
    HumanHandoffNotFoundError,
    HumanHandoffOperationsService,
    HumanHandoffRoutingError,
    HumanHandoffView,
    HumanReplyDeliveryUnavailableError,
    HumanReplyIdempotencyConflictError,
    HumanReplyIdempotencyValidationError,
)
from app.services.operator_analytics import AnalyticsDays, OperatorAnalyticsService
from app.services.operator_views import OperatorViewNotFoundError, OperatorViewService

router = APIRouter(prefix="/operator", tags=["operator"])


def _handoff_read(view: HumanHandoffView) -> OperatorHandoffRead:
    assignment = (
        OperatorAssignmentRead(**view.assignment.__dict__)
        if view.assignment is not None
        else None
    )
    handoff = view.handoff
    return OperatorHandoffRead(
        id=handoff.id,
        lead=OperatorHandoffLeadRead(
            id=view.lead.id,
            full_name=view.lead.full_name,
            company_name=view.lead.company_name,
            job_title=view.lead.job_title,
            email=view.lead.email,
            phone=view.lead.phone,
            source=view.lead.source,
            status=view.lead.status,
            sales_stage=view.lead.sales_stage,
            assignment=assignment,
        ),
        reason_code=handoff.reason_code,
        explanation=handoff.explanation,
        status=handoff.status,
        created_at=handoff.created_at,
        updated_at=handoff.resolved_at or handoff.created_at,
        resolved_at=handoff.resolved_at,
    )


@router.get("/handoffs", response_model=list[OperatorHandoffRead])
def list_operator_handoffs(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: ConversationOperatePermissionDep,
    active_only: Annotated[bool, Query()] = True,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[OperatorHandoffRead]:
    views = HumanHandoffOperationsService(session).list_handoffs(
        workspace,
        active_only=active_only,
        offset=offset,
        limit=limit,
    )
    return [_handoff_read(view) for view in views]


@router.get("/handoffs/{handoff_id}", response_model=OperatorHandoffDetailRead)
def get_operator_handoff(
    handoff_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: ConversationOperatePermissionDep,
    context_limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> OperatorHandoffDetailRead:
    try:
        view = HumanHandoffOperationsService(session).get_handoff(
            workspace,
            handoff_id,
            context_limit=context_limit,
        )
    except HumanHandoffNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Sales handoff not found") from exc
    summary = _handoff_read(view)
    return OperatorHandoffDetailRead(
        **summary.model_dump(),
        messages=[ConversationMessageRead.model_validate(message) for message in view.messages],
    )


@router.post("/handoffs/{handoff_id}/reply", response_model=HumanHandoffReplyRead)
def send_operator_handoff_reply(
    handoff_id: UUID,
    payload: HumanHandoffReplyCreate,
    session: SessionDep,
    settings: SettingsDep,
    workspace: CurrentWorkspaceDep,
    _: ConversationOperatePermissionDep,
    __: OutboundActionOperatePermissionDep,
    actor: OperatorAssignmentActorDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> HumanHandoffReplyRead:
    try:
        result = HumanHandoffOperationsService.from_settings(session, settings).send_human_reply(
            workspace=workspace,
            handoff_id=handoff_id,
            content=payload.content,
            idempotency_key=idempotency_key or "",
            actor=actor,
        )
    except HumanHandoffNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Active Sales handoff not found") from exc
    except HumanReplyIdempotencyValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HumanReplyIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (HumanHandoffRoutingError, HumanReplyDeliveryUnavailableError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return HumanHandoffReplyRead(
        handoff_id=result.handoff.id,
        lead_id=result.handoff.lead_id,
        outbound_action_id=result.action.id,
        outbound_status=result.action.status,
        delivered=result.action.status == OutboundIntegrationActionStatus.DELIVERED,
        provider_delivery_id=result.action.provider_delivery_id,
        conversation_message=(
            ConversationMessageRead.model_validate(result.message)
            if result.message is not None
            else None
        ),
        duplicate=result.duplicate,
    )


@router.post(
    "/handoffs/{handoff_id}/resolve",
    response_model=OperatorHandoffResolutionRead,
)
def resolve_operator_handoff(
    handoff_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: ConversationOperatePermissionDep,
    actor: OperatorAssignmentActorDep,
) -> OperatorHandoffResolutionRead:
    try:
        result = HumanHandoffOperationsService(session).resolve_handoff(
            workspace=workspace,
            handoff_id=handoff_id,
            actor=actor,
        )
    except HumanHandoffNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Sales handoff not found") from exc
    except HumanHandoffRoutingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    assert result.handoff.resolved_at is not None
    return OperatorHandoffResolutionRead(
        handoff_id=result.handoff.id,
        lead_id=result.handoff.lead_id,
        status=result.handoff.status,
        resolved_at=result.handoff.resolved_at,
        operator_user_id=result.operator_user_id,
        duplicate=result.duplicate,
    )


@router.get("/analytics", response_model=OperatorAnalyticsRead)
def get_operator_analytics(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: WorkspaceReadPermissionDep,
    days: Annotated[int, Query()] = 30,
) -> OperatorAnalyticsRead:
    if days not in (7, 30, 90):
        raise HTTPException(status_code=422, detail="Days must be 7, 30, or 90")
    return OperatorAnalyticsService(session).summarize(workspace, days=cast(AnalyticsDays, days))


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
    return OperatorViewService(session).list_approvals(workspace, status=status, limit=limit)


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
