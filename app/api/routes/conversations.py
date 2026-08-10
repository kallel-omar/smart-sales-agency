from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import CurrentWorkspaceDep, SessionDep, SettingsDep
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.services import SalesDepartmentService
from app.models import ConversationMessage
from app.schemas import ConversationMessageRead, InboundMessage, SalesReply
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.repository import NotFoundError, SalesRepository


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
        lead = repository.get_lead(lead_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    if lead.tenant_id != workspace.slug:
        raise HTTPException(
            status_code=404,
            detail="Lead not found",
        )

    context = AgentContext(
        settings=settings,
        repository=repository,
        llm=None,
        workspace=workspace,
        ai_invocation_gateway=AIInvocationGateway(session, settings),
    )

    sales_department = SalesDepartmentService(context)

    result = await sales_department.draft_sales_reply(
        lead=lead,
        channel=payload.channel,
        content=payload.content,
    )

    return SalesReply(
        lead_id=lead.id,
        detected_stage=result.detected_stage,
        draft_reply=result.draft_reply,
        approval_id=result.approval_id,
        handoff_required=result.handoff_required,
        handoff_reason_code=result.handoff_reason_code,
    )
