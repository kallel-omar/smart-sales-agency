from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.core.work_items import WorkItemStatus
from app.models import (
    AIInvocationStatus,
    ApprovalStatus,
    IntegrationAccountAuditAction,
    LeadStatus,
    OutboundDeliveryFailureClassification,
    OutboundActionPriority,
    OutboundIntegrationActionStatus,
    OutboundIntegrationActionType,
    OutboundIntegrationAuditAction,
    ProviderDeliveryStatus,
    SalesConversationHandoffStatus,
    SalesHandoffReasonCode,
    SalesLanguage,
    SalesStage,
    SalesTone,
    SalesWritingScript,
    WorkspaceMemberRole,
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


class OperatorAssignmentUpdate(BaseModel):
    """Target only; workspace and assigner authority come from trusted context."""

    model_config = ConfigDict(extra="forbid")

    workspace_member_id: UUID


class OperatorAssignmentRead(BaseModel):
    """Compact safe view of the current human responsibility assignment."""

    assigned_to_membership_id: UUID
    assigned_to_user_id: UUID | None = None
    assigned_to_display_name: str | None = None
    assigned_at: datetime | None = None
    assigned_by_user_id: UUID | None = None
    assigned_by_membership_id: UUID | None = None
    assignee_membership_active: bool | None = None
    assignee_user_active: bool | None = None


class LeadRead(LeadCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contact_id: UUID | None = None
    status: LeadStatus
    sales_stage: SalesStage
    score: int
    assignment: OperatorAssignmentRead | None = None
    created_at: datetime
    updated_at: datetime


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CustomerRead(CustomerCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    created_at: datetime
    updated_at: datetime


class ContactCreate(BaseModel):
    customer_id: UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)


class ContactRead(ContactCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
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


class WhatsAppCloudInboundTextEvent(BaseModel):
    """Text-only event normalized by the WhatsApp Cloud transport boundary."""

    model_config = ConfigDict(extra="forbid")

    channel: Literal["whatsapp_cloud"] = "whatsapp_cloud"
    provider_event_id: str = Field(min_length=1, max_length=200)
    sender_external_id: str = Field(min_length=1, max_length=255)
    recipient_account_id: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=10_000)
    timestamp: int | None = Field(default=None, ge=0)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class InboundIntegrationDuplicateRead(BaseModel):
    """Safe acknowledgement for a retry that was already accepted once."""

    duplicate: bool = True
    correlation_id: UUID


class InboundIntegrationReplyRead(BaseModel):
    """Safe first-delivery response for a durably correlated inbound event."""

    lead_id: UUID
    detected_stage: SalesStage
    draft_reply: str
    approval_id: UUID | None = None
    handoff_required: bool = False
    handoff_reason_code: SalesHandoffReasonCode | None = None
    correlation_id: UUID


class IntegrationExecutionInboundReceiptRead(BaseModel):
    """Safe persisted inbound receipt for one correlated integration execution."""

    integration_account_id: UUID
    provider: str
    external_account_id: str | None
    external_event_id: str
    correlation_id: UUID
    received_at: datetime


class IntegrationExecutionDeliveryAttemptRead(BaseModel):
    """Safe delivery attempt nested under its correlated outbound action."""

    id: UUID
    attempt_number: int
    status: OutboundIntegrationActionStatus
    provider_delivery_id: str | None
    started_at: datetime
    completed_at: datetime | None
    failure_code: str | None
    failure_message: str | None


class IntegrationExecutionOutboundActionRead(BaseModel):
    """Safe read-only outbound action projection for an integration trace."""

    id: UUID
    integration_account_id: UUID
    provider: str
    external_target_id: str
    action_type: OutboundIntegrationActionType
    status: OutboundIntegrationActionStatus
    requires_approval: bool
    approval_request_id: UUID | None
    approval_status: ApprovalStatus | None
    provider_delivery_id: str | None
    delivered_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None
    expired_at: datetime | None
    created_at: datetime
    delivery_attempts: list[IntegrationExecutionDeliveryAttemptRead]


class IntegrationExecutionTraceRead(BaseModel):
    """Safe workspace-scoped composition of existing integration execution records."""

    correlation_id: UUID
    inbound: IntegrationExecutionInboundReceiptRead
    outbound_actions: list[IntegrationExecutionOutboundActionRead]


class AIInvocationUsageRead(BaseModel):
    """Safe AI accounting metadata; prompt, response, and credential data is absent."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    conversation_id: UUID | None
    task_identifier: str
    agent_identifier: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    estimated_cost: Decimal | None
    pricing_known: bool
    status: AIInvocationStatus
    created_at: datetime


class DepartmentRead(BaseModel):
    """Safe persisted Department projection scoped to one workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    kind: DepartmentKind
    created_at: datetime
    updated_at: datetime


class CapabilityRead(BaseModel):
    """Safe persisted business Capability projection scoped to one workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    department_id: UUID
    key: BusinessCapabilityKey
    active: bool
    created_at: datetime
    updated_at: datetime


class AIEmployeeRead(BaseModel):
    """Safe persisted AIEmployee projection scoped to one workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    department_id: UUID
    role_key: AIEmployeeRoleKey
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class AIEmployeeCapabilityAssignmentRead(BaseModel):
    """Safe persisted AIEmployee-Capability assignment scoped to one workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    ai_employee_id: UUID
    capability_id: UUID
    created_at: datetime


class AIEmployeeCapabilityToolAccessRead(BaseModel):
    """Safe AIEmployee-Capability integration-action grant scoped to one workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    assignment_id: UUID
    integration_account_id: UUID
    action_type: OutboundIntegrationActionType
    autonomy_level: AIEmployeeAutonomyLevel
    active: bool
    created_at: datetime
    updated_at: datetime


class WorkItemRead(BaseModel):
    """Safe generic WorkItem projection scoped to one workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    department_id: UUID
    ai_employee_id: UUID | None
    capability_id: UUID | None
    assignment_id: UUID | None
    status: WorkItemStatus
    work_type: str
    title: str
    input: dict[str, Any]
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    correlation_id: UUID
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None


class AIInvocationUsageSummaryRead(BaseModel):
    """Safe workspace-only AI usage aggregate without prompt or credential data."""

    invocation_count: int
    successful_invocation_count: int
    failed_invocation_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    unknown_token_usage_invocation_count: int
    known_estimated_spend: Decimal
    unknown_pricing_invocation_count: int


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


class ProviderDeliveryStatusEventCreate(BaseModel):
    """Machine-authenticated provider status callback with no trusted tenancy."""

    model_config = ConfigDict(extra="forbid")

    provider_delivery_id: str = Field(min_length=1, max_length=255)
    provider_status: ProviderDeliveryStatus
    provider_timestamp: datetime | None = None
    provider_error_code: str | None = Field(default=None, max_length=100)
    provider_error_title: str | None = Field(default=None, max_length=200)
    provider_error_type: str | None = Field(default=None, max_length=100)
    failure_classification: OutboundDeliveryFailureClassification | None = None

    @field_validator("provider_delivery_id")
    @classmethod
    def normalize_provider_delivery_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider_delivery_id is required")
        if any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in normalized
        ):
            raise ValueError(
                "provider_delivery_id must not contain whitespace or control characters"
            )
        return normalized

    @field_validator("provider_error_code", "provider_error_title", "provider_error_type")
    @classmethod
    def normalize_safe_provider_error_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
            raise ValueError("provider error metadata must not contain control characters")
        return normalized


class ProviderDeliveryStatusEventRead(BaseModel):
    """Safe provider status history record with no raw webhook or credentials."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    integration_account_id: UUID
    outbound_integration_action_id: UUID
    provider_delivery_id: str
    provider_status: ProviderDeliveryStatus
    provider_timestamp: datetime | None
    provider_error_code: str | None
    provider_error_title: str | None
    provider_error_type: str | None
    failure_classification: OutboundDeliveryFailureClassification | None
    created_at: datetime


class ProviderDeliveryStatusEventIngestRead(BaseModel):
    """Safe acknowledgement for a provider delivery-status callback."""

    duplicate: bool
    event: ProviderDeliveryStatusEventRead


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
    archived_at: datetime | None
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
    decided_by_user_id: UUID | None = None
    decided_by_membership_id: UUID | None = None
    decided_by_role: WorkspaceMemberRole | None = None


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


class IntegrationRuntimeReadinessBlockerRead(BaseModel):
    """Safe explanation for one deterministic configuration blocker."""

    code: str
    message: str


class IntegrationRuntimeCapabilityReadinessRead(BaseModel):
    """Safe configuration readiness for one provider-neutral capability."""

    capability: str
    supported: bool
    ready: bool
    blocking_reasons: list[str]
    blocking_reason_details: list[IntegrationRuntimeReadinessBlockerRead]


class IntegrationRuntimeReadinessRead(BaseModel):
    """Configuration-only readiness; external availability is never probed."""

    id: UUID
    provider: str
    active: bool
    status: str
    configuration_ready: bool
    external_provider_availability_checked: bool = False
    supported_capabilities: list[str]
    capability_readiness: list[IntegrationRuntimeCapabilityReadinessRead]
    blocking_reasons: list[str]
    blocking_reason_details: list[IntegrationRuntimeReadinessBlockerRead]


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
    archived_outbound_action_count: int
    unarchived_outbound_action_count: int


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
    work_item_id: UUID | None = None
    action_type: str
    channel: str
    payload: dict[str, Any]
    status: ApprovalStatus
    reviewer_note: str | None
    created_at: datetime
    decided_at: datetime | None
    decided_by_user_id: UUID | None = None
    decided_by_membership_id: UUID | None = None
    decided_by_role: WorkspaceMemberRole | None = None
    assignment: OperatorAssignmentRead | None = None


class SalesReply(BaseModel):
    lead_id: UUID
    detected_stage: SalesStage
    draft_reply: str
    approval_id: UUID | None = None
    handoff_required: bool = False
    handoff_reason_code: SalesHandoffReasonCode | None = None


class DirectSalesReply(SalesReply):
    """Direct API reply with an optional retry-replay indicator."""

    duplicate: bool | None = None


class SalesHandoffResolutionRead(BaseModel):
    """Safe response for an explicit Sales handoff resolution."""

    lead_id: UUID
    reason_code: SalesHandoffReasonCode
    status: SalesConversationHandoffStatus
    created_at: datetime
    resolved_at: datetime

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
    model_config = ConfigDict(extra="forbid")

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


class UserCreate(BaseModel):
    """Safe identity input; credentials are deliberately not part of this schema."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)


class UserRead(BaseModel):
    """Safe persisted user identity with no credentials or token material."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class AuthRegistrationCreate(BaseModel):
    """Registration payload; Pydantic's secret wrapper reduces accidental repr leakage."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: SecretStr
    display_name: str | None = Field(default=None, max_length=200)


class AuthLoginCreate(BaseModel):
    """Credential input for the human access-token exchange."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: SecretStr


class AccessTokenRead(BaseModel):
    """Safe short-lived bearer-token response with no credential metadata."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class WorkspaceMemberCreate(BaseModel):
    """Membership data without workspace authority, which must come from the caller."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    role: WorkspaceMemberRole


class WorkspaceMemberRead(BaseModel):
    """Safe membership representation for future authenticated workspace views."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    user_id: UUID
    role: WorkspaceMemberRole
    active: bool
    created_at: datetime


class WorkspaceSalesInstructionsUpdate(BaseModel):
    """Trusted workspace-admin configuration; ownership is request-derived."""

    model_config = ConfigDict(extra="forbid")

    # Content constraints deliberately live in the workspace service so blank
    # replacements and normalized-length checks follow one deterministic path.
    instructions: str


class WorkspaceSalesInstructionsRead(BaseModel):
    """Safe view of the current workspace's Sales instructions only."""

    sales_instructions: str | None


class WorkspaceSalesCommunicationUpdate(BaseModel):
    """Trusted workspace-admin language and tone preferences; ownership is request-derived."""

    model_config = ConfigDict(extra="forbid")

    preferred_language: SalesLanguage | None = None
    preferred_script: SalesWritingScript | None = None
    preferred_tone: SalesTone | None = None


class WorkspaceSalesCommunicationRead(BaseModel):
    """Safe view of the current workspace's Sales communication preferences only."""

    preferred_language: SalesLanguage | None
    preferred_script: SalesWritingScript | None
    preferred_tone: SalesTone | None
