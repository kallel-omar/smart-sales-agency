from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from sqlmodel import Session

from app.api.dependencies import (
    ConversationOperatePermissionDep,
    CurrentWorkspaceDep,
    OperatorAssignmentActorDep,
    OperatorAssignmentManagePermissionDep,
    SessionDep,
    SettingsDep,
)
from app.departments.sales.services import (
    DirectConversationTurnIdempotencyConflictError,
    DirectConversationTurnIdempotencyValidationError,
    DirectSalesConversationTurnService,
    SalesConversationHandoffService,
    SalesConversationTurnInput,
)
from app.models import ConversationMessage, Lead
from app.schemas import (
    ConversationMessageRead,
    DirectSalesReply,
    InboundMessage,
    LeadRead,
    OperatorAssignmentRead,
    OperatorAssignmentUpdate,
    SalesHandoffResolutionRead,
)
from app.services.operator_assignments import (
    OperatorAssignmentActorWorkspaceMismatchError,
    OperatorAssignmentNotFoundError,
    OperatorAssignmentService,
)
from app.services.repository import HandoffLifecycleConflictError, NotFoundError, SalesRepository

router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
)


def lead_read(session: Session, lead: Lead) -> LeadRead:
    snapshot = OperatorAssignmentService(session).resolve_lead_assignment(lead)
    assignment = OperatorAssignmentRead(**snapshot.__dict__) if snapshot is not None else None
    return LeadRead.model_validate(lead).model_copy(update={"assignment": assignment})


@router.get(
    "/{lead_id}",
    response_model=list[ConversationMessageRead],
)
def get_conversation_history(
    lead_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: ConversationOperatePermissionDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ConversationMessage]:
    repository = SalesRepository(session)

    try:
        lead = repository.get_lead(lead_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Lead not found",
        ) from exc

    if lead.tenant_id != workspace.slug:
        raise HTTPException(
            status_code=404,
            detail="Lead not found",
        )

    return repository.conversation_history(
        lead_id,
        limit=limit,
    )


@router.put(
    "/{lead_id}/assignment",
    response_model=LeadRead,
)
def assign_conversation_operator(
    lead_id: UUID,
    payload: OperatorAssignmentUpdate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OperatorAssignmentManagePermissionDep,
    actor: OperatorAssignmentActorDep,
) -> LeadRead:
    try:
        lead = OperatorAssignmentService(session).assign_lead(
            workspace=workspace,
            lead_id=lead_id,
            target_membership_id=payload.workspace_member_id,
            actor=actor,
        )
    except (
        OperatorAssignmentNotFoundError,
        OperatorAssignmentActorWorkspaceMismatchError,
    ) as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc
    return lead_read(session, lead)


@router.delete(
    "/{lead_id}/assignment",
    response_model=LeadRead,
)
def clear_conversation_operator(
    lead_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: OperatorAssignmentManagePermissionDep,
    actor: OperatorAssignmentActorDep,
) -> LeadRead:
    try:
        lead = OperatorAssignmentService(session).clear_lead(
            workspace=workspace,
            lead_id=lead_id,
            actor=actor,
        )
    except (
        OperatorAssignmentNotFoundError,
        OperatorAssignmentActorWorkspaceMismatchError,
    ) as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc
    return lead_read(session, lead)


@router.post(
    "/{lead_id}/reply",
    response_model=DirectSalesReply,
    response_model_exclude_none=True,
)
async def draft_sales_reply(
    lead_id: UUID,
    payload: InboundMessage,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: ConversationOperatePermissionDep,
    settings: SettingsDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DirectSalesReply:
    repository = SalesRepository(session)

    try:
        outcome = await DirectSalesConversationTurnService(
            repository=repository,
            settings=settings,
            workspace=workspace,
        ).process(
            SalesConversationTurnInput(
                lead_id=lead_id,
                customer_message=payload.content,
                channel=payload.channel,
            ),
            idempotency_key=idempotency_key,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc
    except DirectConversationTurnIdempotencyValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DirectConversationTurnIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    result = outcome.turn_result
    return DirectSalesReply(
        lead_id=result.lead_id,
        detected_stage=result.detected_stage,
        draft_reply=result.draft_reply,
        approval_id=result.approval_id,
        handoff_required=result.handoff_required,
        handoff_reason_code=result.handoff_reason_code,
        duplicate=True if outcome.duplicate else None,
    )


@router.post(
    "/{lead_id}/handoff/resolve",
    response_model=SalesHandoffResolutionRead,
)
def resolve_sales_handoff(
    lead_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: ConversationOperatePermissionDep,
) -> SalesHandoffResolutionRead:
    """Explicitly resolve the current workspace-scoped Sales handoff."""

    try:
        result = SalesConversationHandoffService(
            repository=SalesRepository(session),
            workspace=workspace,
        ).resolve_active_handoff(lead_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc
    except HandoffLifecycleConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return SalesHandoffResolutionRead(
        lead_id=result.lead_id,
        reason_code=result.reason_code,
        status=result.status,
        created_at=result.created_at,
        resolved_at=result.resolved_at,
    )
