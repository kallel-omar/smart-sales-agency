from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    ApprovalStatus,
    IntegrationAccountAuditAction,
    LeadStatus,
    OutboundIntegrationActionStatus,
    OutboundIntegrationActionType,
    SalesStage,
)


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


class InboundIntegrationEvent(BaseModel):
    """Provider-neutral inbound event accepted at the integration boundary."""

    model_config = ConfigDict(extra="forbid")

    lead_id: UUID
    channel: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=10_000)
    external_event_id: str | None = Field(default=None, max_length=200)


class IntegrationAccountProvision(BaseModel):
    """Provider-neutral account data needed to provision inbound access."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=100)
    external_account_id: str | None = Field(default=None, max_length=255)
    # Accepted only to link the account to a configured secret backend. It is
    # deliberately excluded from normal account responses.
    secret_reference: str = Field(min_length=1, max_length=255)


class IntegrationAccountSecretReferenceUpdate(BaseModel):
    """Internal secret-backend reference update for an integration account."""

    model_config = ConfigDict(extra="forbid")

    secret_reference: str = Field(min_length=1, max_length=255)


class IntegrationAccountRead(BaseModel):
    """Safe integration-account representation that never exposes credential hashes."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    provider: str
    external_account_id: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class IntegrationAccountCredentialRead(IntegrationAccountRead):
    """Returned only when an inbound credential is first issued or rotated."""

    inbound_credential: str


class IntegrationAccountAuditEventRead(BaseModel):
    """Safe integration-account lifecycle history representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    integration_account_id: UUID
    action: IntegrationAccountAuditAction
    created_at: datetime


class IntegrationAccountAuditRetentionCleanupRead(BaseModel):
    """Safe response for explicitly removing expired audit history."""

    deleted_count: int
    cutoff: datetime


class OutboundIntegrationActionCreate(BaseModel):
    """Provider-neutral outbound delivery intent for a scoped account."""

    model_config = ConfigDict(extra="forbid")

    external_target_id: str = Field(min_length=1, max_length=255)
    action_type: OutboundIntegrationActionType
    content: str = Field(min_length=1, max_length=10_000)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


class OutboundIntegrationActionRead(BaseModel):
    """Safe delivery-intent response with no credentials or arbitrary payload."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    integration_account_id: UUID
    provider: str
    external_target_id: str
    action_type: OutboundIntegrationActionType
    content: str
    correlation_id: str | None
    status: OutboundIntegrationActionStatus
    provider_delivery_id: str | None
    delivered_at: datetime | None
    failed_at: datetime | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime


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

class WorkspaceCreate(BaseModel):
    slug: str
    name: str


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime
