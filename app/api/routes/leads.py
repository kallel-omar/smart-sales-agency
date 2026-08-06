from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app.api.dependencies import CurrentWorkspaceDep, SessionDep
from app.models import Lead
from app.schemas import LeadCreate, LeadRead
from app.services.workspaces import (
    WorkspaceInactiveError,
    WorkspaceNotFoundError,
    require_active_workspace,
)

router = APIRouter(prefix="/leads", tags=["leads"])


@router.post("", response_model=LeadRead, status_code=201)
def create_lead(
    payload: LeadCreate,
    session: SessionDep,
) -> Lead:
    try:
        workspace = require_active_workspace(
            session,
            payload.tenant_id,
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except WorkspaceInactiveError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    lead_data = payload.model_dump()
    lead_data["tenant_id"] = workspace.slug

    lead = Lead(**lead_data)

    session.add(lead)
    session.commit()
    session.refresh(lead)

    return lead


@router.get("", response_model=list[LeadRead])
def list_leads(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> list[Lead]:
    statement = (
        select(Lead)
        .where(Lead.tenant_id == workspace.slug)
        .order_by(Lead.created_at.desc())
    )

    return list(session.exec(statement).all())

@router.get("/{lead_id}", response_model=LeadRead)
def get_lead(
    lead_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> Lead:
    lead = session.get(Lead, lead_id)

    if not lead or lead.tenant_id != workspace.slug:
        raise HTTPException(
            status_code=404,
            detail="Lead not found",
        )

    return lead
