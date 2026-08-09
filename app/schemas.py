from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import (
    ApprovalStatus,
    IntegrationAccountAuditAction,
    LeadStatus,
    OutboundIntegrationActionStatus,
    OutboundActionPriority,
    OutboundIntegrationActionType,
    OutboundIntegrationAuditAction,
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
    expires_at: datetime | None = None
    requires_approval: bool = False
    not_before: datetime | None = None

    @field_validator("not_before")
    @classmethod
    def normalize_not_before_to_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("not_before must be timezone-aware UTC time")
        return value.astimezone(timezone.utc)


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
    requires_approval: bool
    approval_request_id: UUID | None
    owner_reference: str | None
    archived_at: datetime | None
    status: OutboundIntegrationActionStatus
    provider_delivery_id: str | None
    delivered_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None
    not_before: datetime | None
    expires_at: datetime | None
    expired_at: datetime | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime


class OutboundIntegrationActionSummaryRead(BaseModel):
    """Safe operational list representation with no outbound content or secrets."""

    id: UUID
    integration_account_id: UUID
    provider: str
    external_target_id: str
    action_type: OutboundIntegrationActionType
    status: OutboundIntegrationActionStatus
    priority: OutboundActionPriority
    owner_reference: str | None
    archived_at: datetime | None
    provider_delivery_id: str | None
    delivered_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None
    not_before: datetime | None
    expires_at: datetime | None
    expired_at: datetime | None
    failure_code: str | None
    created_at: datetime


class OutboundActionAnnotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)


class OutboundActionAnnotationRead(BaseModel):
    id: UUID
    outbound_integration_action_id: UUID
    text: str
    created_at: datetime


class OutboundActionLabelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=64)


class OutboundActionLabelRead(BaseModel):
    label: str
    created_at: datetime


class OutboundActionPriorityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: OutboundActionPriority


class OutboundActionOwnerReferenceUpdate(BaseModel):
    """Opaque future-operator reference; identity validation is intentionally deferred."""

    model_config = ConfigDict(extra="forbid")
    owner_reference: str | None = Field(default=None, max_length=200)


class OutboundIntegrationActionDetailRead(OutboundIntegrationActionSummaryRead):
    """Safe single-action operational view with no outbound request content."""

    failure_message: str | None


class OutboundIntegrationDeliveryAttemptRead(BaseModel):
    """Safe outbound delivery-attempt history with no request or secret data."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    integration_account_id: UUID
    outbound_integration_action_id: UUID
    attempt_number: int
    status: OutboundIntegrationActionStatus
    provider_delivery_id: str | None
    started_at: datetime
    completed_at: datetime | None
    failure_code: str | None
    failure_message: str | None


class OutboundIntegrationAuditEventRead(BaseModel):
    """Safe outbound lifecycle audit record with no request or secret data."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    integration_account_id: UUID
    outbound_integration_action_id: UUID
    action: OutboundIntegrationAuditAction
    created_at: datetime


class OutboundActionStateHistoryEntryRead(BaseModel):
    """Safe, immutable state-transition history for one outbound action."""

    state: OutboundIntegrationActionStatus
    event: OutboundIntegrationAuditAction
    created_at: datetime


class OutboundActionTimelineEntryRead(BaseModel):
    """Safe chronological entry composed from existing outbound records."""

    category: str
    event: str
    message: str
    created_at: datetime
    state: OutboundIntegrationActionStatus | None
    attempt_number: int | None


class OutboundActionTransitionValidationRead(BaseModel):
    """Safe, read-only preflight result for a requested state transition."""

    allowed: bool
    current_state: OutboundIntegrationActionStatus
    requested_target: OutboundIntegrationActionStatus
    denial_reason: str | None
    denial_reason_detail: "OutboundActionTransitionExplanationRead | None"


class OutboundActionTransitionExplanationRead(BaseModel):
    """Safe structured explanation for a denied state-transition preflight."""

    code: str
    message: str
    delivered_at: datetime | None
    cancelled_at: datetime | None
    expired_at: datetime | None


class OutboundIntegrationDeliveryStatusRead(BaseModel):
    """Safe read-only summary of an outbound action and retry eligibility."""

    id: UUID
    provider: str
    external_target_id: str
    action_type: OutboundIntegrationActionType
    status: OutboundIntegrationActionStatus
    created_at: datetime
    provider_delivery_id: str | None
    delivered_at: datetime | None
    failed_at: datetime | None
    failure_code: str | None
    failure_message: str | None
    attempt_count: int
    retry_allowed: bool
    retry_denial_reason: str | None
    next_retry_at: datetime | None


class OutboundApprovalStatusRead(BaseModel):
    """Safe, read-only approval state for one outbound action."""

    action_id: UUID
    requires_approval: bool
    approval_request_id: UUID | None
    approval_status: ApprovalStatus | None


class OutboundDeliveryReadinessRead(BaseModel):
    """Safe, read-only answer to whether an outbound action can run now."""

    action_id: UUID
    status: OutboundIntegrationActionStatus
    ready: bool
    blocking_reasons: list[str]
    next_retry_at: datetime | None
    blocking_reason_details: list["OutboundDeliveryReadinessExplanationRead"]


class OutboundDeliveryReadinessExplanationRead(BaseModel):
    """Safe structured explanation for one readiness blocking reason."""

    code: str
    message: str
    not_before: datetime | None
    expires_at: datetime | None
    next_retry_at: datetime | None


class OutboundActionExpirationCleanupRead(BaseModel):
    deleted_count: int
    cutoff: datetime


class IntegrationAccountHealthRead(BaseModel):
    """Safe persisted-state health summary for one integration account."""

    id: UUID
    provider: str
    active: bool
    health: str
    most_recent_outbound_at: datetime | None
    recent_delivered_count: int
    recent_failed_count: int
    pending_action_count: int
    failed_action_count: int


class IntegrationOperationalSummaryRead(BaseModel):
    """Safe, workspace-level operational aggregate for integration accounts."""

    active_integration_account_count: int
    pending_outbound_action_count: int
    delivered_outbound_action_count: int
    failed_outbound_action_count: int
    retryable_failed_action_count: int
    cancelled_outbound_action_count: int
    expired_outbound_action_count: int
    most_recent_outbound_at: datetime | None
    recent_delivered_count: int
    recent_failed_count: int
    priority_counts: dict[OutboundActionPriority, int]
    owned_outbound_action_count: int
    unowned_outbound_action_count: int


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
    lead_id: UUID | None
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
