from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from app.api.dependencies import SessionDep
from app.models import Lead
from app.schemas import LeadCreate, LeadRead

router = APIRouter(prefix="/leads", tags=["leads"])


@router.post("", response_model=LeadRead, status_code=201)
def create_lead(payload: LeadCreate, session: SessionDep) -> Lead:
    lead = Lead(**payload.model_dump())
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


@router.get("", response_model=list[LeadRead])
def list_leads(session: SessionDep, tenant_id: str = Query(default="demo")) -> list[Lead]:
    statement = select(Lead).where(Lead.tenant_id == tenant_id).order_by(Lead.created_at.desc())
    return list(session.exec(statement).all())


@router.get("/{lead_id}", response_model=LeadRead)
def get_lead(lead_id: UUID, session: SessionDep) -> Lead:
    lead = session.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead
