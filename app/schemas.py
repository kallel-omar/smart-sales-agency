from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import ApprovalStatus, LeadStatus, SalesStage


class LeadCreate(BaseModel):
    tenant_id: str = "demo"
    full_name: str = Field(min_length=2, max_length=200)
    company_name: str = Field(min_length=2, max_length=200)
    job_title: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    source: str = "manual"
    notes: str | None = None


class LeadRead(LeadCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: LeadStatus
    score: int
    created_at: datetime
    updated_at: datetime


class ProductCreate(BaseModel):
    tenant_id: str = "demo"
    name: str
    description: str
    price: float | None = None
    minimum_price: float | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class InboundMessage(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    channel: str = "console"


class WorkflowResult(BaseModel):
    lead_id: UUID
    status: str
    score: int
    qualified: bool
    research_summary: str
    draft_message: str | None = None
    approval_id: UUID | None = None
    next_action: str


class ApprovalDecision(BaseModel):
    reviewer_note: str | None = None


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    action_type: str
    channel: str
    payload: dict[str, Any]
    status: ApprovalStatus
    reviewer_note: str | None
    created_at: datetime
    decided_at: datetime | None


class SalesReply(BaseModel):
    lead_id: UUID
    detected_stage: SalesStage
    draft_reply: str
    approval_id: UUID | None = None

class ConversationMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    direction: str
    channel: str
    stage: SalesStage
    content: str
    created_at: datetime
