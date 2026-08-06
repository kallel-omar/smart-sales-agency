from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.agents.base import AgentContext
from app.agents.sales_agent import SalesConversationAgent
from app.api.dependencies import CurrentWorkspaceDep, SessionDep, SettingsDep
from app.models import ConversationMessage
from app.schemas import ConversationMessageRead, InboundMessage, SalesReply
from app.services.llm import build_llm
from app.services.repository import NotFoundError, SalesRepository

router = APIRouter(prefix="/conversations", tags=["conversations"])



@router.get("/{lead_id}", response_model=list[ConversationMessageRead])
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

@router.post("/{lead_id}/reply", response_model=SalesReply)
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
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    if lead.tenant_id != workspace.slug:
        raise HTTPException(
            status_code=404,
            detail="Lead not found",
        )

    context = AgentContext(settings=settings, repository=repository, llm=build_llm(settings))
    agent = SalesConversationAgent(context)
    stage, reply = await agent.draft_reply(lead, payload.content)

    repository.add_message(
        ConversationMessage(
            lead_id=lead.id,
            direction="inbound",
            channel=payload.channel,
            stage=stage,
            content=payload.content,
        )
    )

    approval_id = None
    if settings.require_human_approval:
        approval = repository.create_approval(
            lead_id=lead.id,
            channel=payload.channel,
            payload={"recipient": lead.email or lead.phone or lead.full_name,
                      "content": reply,
                      "stage": stage.value,
                      },
        )
        approval_id = approval.id
    else:
        repository.add_message(
            ConversationMessage(
                lead_id=lead.id,
                direction="outbound",
                channel=payload.channel,
                stage=stage,
                content=reply,
            )
        )

    return SalesReply(
        lead_id=lead.id,
        detected_stage=stage,
        draft_reply=reply,
        approval_id=approval_id,
    )
