from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlmodel import Session, select

from app.api.dependencies import (
    CurrentWorkspaceDep,
    SalesDataReadPermissionDep,
    SalesDataWritePermissionDep,
    SessionDep,
)
from app.core.lead_capture import LeadCaptureSignal
from app.models import Lead
from app.schemas import LeadCreate, LeadRead, OperatorAssignmentRead
from app.services.lead_capture import LeadCaptureService
from app.services.operator_assignments import OperatorAssignmentService
from app.services.workspaces import ensure_workspace_lead_capture_foundation

router = APIRouter(prefix="/leads", tags=["leads"])


def lead_read(session: Session, lead: Lead) -> LeadRead:
    snapshot = OperatorAssignmentService(session).resolve_lead_assignment(lead)
    assignment = OperatorAssignmentRead(**snapshot.__dict__) if snapshot is not None else None
    return LeadRead.model_validate(lead).model_copy(update={"assignment": assignment})


@router.post("", response_model=LeadRead, status_code=201)
def create_lead(
    payload: LeadCreate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: SalesDataWritePermissionDep,
) -> LeadRead:
    ensure_workspace_lead_capture_foundation(session, workspace)
    result = LeadCaptureService(session).capture(
        workspace.id,
        LeadCaptureSignal(
            source=payload.source,
            name=payload.full_name,
            email=payload.email,
            phone=payload.phone,
            company_name=payload.company_name,
        ),
    )
    lead = session.get(Lead, result.lead_id)
    if lead is None:
        raise HTTPException(status_code=500, detail="Lead capture failed")
    lead.job_title = payload.job_title
    lead.website = payload.website
    lead.notes = payload.notes
    session.add(lead)
    session.commit()
    session.refresh(lead)

    return lead_read(session, lead)


@router.get("", response_model=list[LeadRead])
def list_leads(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: SalesDataReadPermissionDep,
) -> list[LeadRead]:
    statement = (
        select(Lead)
        .where(Lead.tenant_id == workspace.slug)
        .order_by(Lead.created_at.desc())
    )

    return [lead_read(session, lead) for lead in session.exec(statement).all()]

@router.get("/{lead_id}", response_model=LeadRead)
def get_lead(
    lead_id: UUID,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: SalesDataReadPermissionDep,
) -> LeadRead:
    lead = session.get(Lead, lead_id)

    if not lead or lead.tenant_id != workspace.slug:
        raise HTTPException(
            status_code=404,
            detail="Lead not found",
        )

    return lead_read(session, lead)
