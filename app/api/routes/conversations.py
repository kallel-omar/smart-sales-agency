from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import CurrentWorkspaceDep, SessionDep, SettingsDep
from app.departments.sales.services import (
    SalesConversationHandoffService,
    SalesConversationTurnInput,
    SalesConversationTurnService,
)
from app.models import ConversationMessage
from app.schemas import (
    ConversationMessageRead,
    InboundMessage,
    SalesHandoffResolutionRead,
    SalesReply,
)
from app.services.repository import HandoffLifecycleConflictError, NotFoundError, SalesRepository


router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
)


@router.get(
    "/{lead_id}",
    response_model=list[ConversationMessageRead],
)
def get_conversation_history(
    lead_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
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


@router.post(
    "/{lead_id}/reply",
    response_model=SalesReply,
)
async def draft_sales_reply(
    lead_id: UUID,
    payload: InboundMessage,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    settings: SettingsDep,
) -> SalesReply:
    repository = SalesRepository(session)

    try:
        result = await SalesConversationTurnService(
            repository=repository,
            settings=settings,
            workspace=workspace,
        ).process(
            SalesConversationTurnInput(
                lead_id=lead_id,
                customer_message=payload.content,
                channel=payload.channel,
            )
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Lead not found") from exc

    return SalesReply(
        lead_id=result.lead_id,
        detected_stage=result.detected_stage,
        draft_reply=result.draft_reply,
        approval_id=result.approval_id,
        handoff_required=result.handoff_required,
        handoff_reason_code=result.handoff_reason_code,
    )


@router.post(
    "/{lead_id}/handoff/resolve",
    response_model=SalesHandoffResolutionRead,
)
def resolve_sales_handoff(
    lead_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
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
