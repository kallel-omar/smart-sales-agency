from uuid import UUID

from fastapi import APIRouter, HTTPException

from  app.departments.sales.agents.base import AgentContext
from app.api.dependencies import (
    CurrentWorkspaceDep,
    SessionDep,
    SettingsDep,
)
from app.departments.sales.workflows import NewLeadWorkflow
from app.schemas import WorkflowResult
from app.services.llm import build_llm
from app.services.repository import NotFoundError, SalesRepository

router = APIRouter(
    prefix="/workflows",
    tags=["workflows"],
)


@router.post(
    "/{lead_id}/run",
    response_model=WorkflowResult,
)
async def run_lead_workflow(
    lead_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    settings: SettingsDep,
) -> WorkflowResult:
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

    context = AgentContext(
        settings=settings,
        repository=repository,
        llm=build_llm(settings),
    )

    workflow = NewLeadWorkflow(context)

    try:
        result = await workflow.run(lead_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    return WorkflowResult(
        lead_id=lead_id,
        status=result["status"],
        score=result["score"],
        qualified=result["qualified"],
        research_summary=result["research"]["summary"],
        draft_message=result.get("draft_message"),
        approval_id=result.get("approval_id"),
        next_action=result["next_action"],
    )