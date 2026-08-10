from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, Numeric, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LeadStatus(StrEnum):
    NEW = "new"
    RESEARCHED = "researched"
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


class SalesStage(StrEnum):
    INTRODUCTION = "introduction"
    DISCOVERY = "discovery"
    QUALIFICATION = "qualification"
    VALUE_PROPOSITION = "value_proposition"
    OBJECTION_HANDLING = "objection_handling"
    CLOSING = "closing"
    FOLLOW_UP = "follow_up"


class SalesLanguage(StrEnum):
    """Supported deterministic language modes for customer-facing Sales replies."""

    ENGLISH = "english"
    FRENCH = "french"
    ARABIC = "arabic"
    TUNISIAN_ARABIC = "tunisian_arabic"


class SalesTone(StrEnum):
    """Small, provider-neutral tone choices for customer-facing Sales replies."""

    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    CONCISE = "concise"


class SalesHandoffReasonCode(StrEnum):
    """Stable provider-neutral reasons for a required Sales human handoff."""

    HUMAN_REQUESTED = "human_requested"
    UNSUPPORTED_DISCOUNT_REQUEST = "unsupported_discount_request"
    CUSTOM_PRICING_REQUIRED = "custom_pricing_required"
    UNSUPPORTED_COMMERCIAL_COMMITMENT = "unsupported_commercial_commitment"
    AUTHORITATIVE_INFORMATION_UNAVAILABLE = "authoritative_information_unavailable"
    APPROVAL_REQUIRED = "approval_required"


class SalesConversationHandoffStatus(StrEnum):
    """Explicit lifecycle states for a Sales conversation handoff."""

    ACTIVE = "active"
    RESOLVED = "resolved"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"


class IntegrationAccountAuditAction(StrEnum):
    PROVISIONED = "provisioned"
    CREDENTIAL_ROTATED = "credential_rotated"
    DEACTIVATED = "deactivated"
    REACTIVATED = "reactivated"
    SECRET_REFERENCE_CHANGED = "secret_reference_changed"


class OutboundIntegrationActionType(StrEnum):
    SEND_MESSAGE = "send_message"
    SEND_MEDIA = "send_media"


class OutboundIntegrationActionStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class OutboundActionPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class OutboundDeliveryFailureClassification(StrEnum):
    TEMPORARY = "temporary"
    PERMANENT = "permanent"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


class OutboundIntegrationAuditAction(StrEnum):
    """Safe, provider-neutral lifecycle events for outbound actions."""

    CREATED = "created"
    DELIVERY_ATTEMPTED = "delivery_attempted"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRIED = "retried"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    UNARCHIVED = "unarchived"


class AIInvocationStatus(StrEnum):
    """Safe terminal outcome for one provider-neutral AI invocation."""

    SUCCESSFUL = "successful"
    FAILED = "failed"


class Workspace(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    slug: str = Field(
        sa_column=Column(
            String(100),
            unique=True,
            index=True,
            nullable=False,
        )
    )

    name: str = Field(max_length=200)
    active: bool = Field(default=True)
    # Optional server-owned AI usage policy. A null limit means that dimension
    # is not constrained for this workspace. Usage itself remains in
    # AIInvocationUsage; these are configuration values, not counters.
    ai_invocation_limit: int | None = Field(default=None, ge=0)
    ai_total_token_limit: int | None = Field(default=None, ge=0)
    ai_estimated_spend_limit: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 8), nullable=True),
    )
    # JSON keeps policy configuration workspace-owned without coupling it to a
    # provider. Values are validated by the deterministic limit policy before
    # use; they are intentionally not exposed through public workspace reads.
    ai_permitted_model_tiers: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    ai_model_tier_downgrade_mappings: dict[str, str] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    # Trusted workspace-administrator configuration used as one distinct
    # Sales prompt section. It is not customer conversation content and never
    # stores rendered prompts or provider data.
    sales_instructions: str | None = Field(default=None, max_length=4_000)
    # Optional trusted administrator defaults. The current customer message is
    # otherwise classified deterministically at runtime; no conversation data
    # is persisted as workspace configuration.
    sales_preferred_language: SalesLanguage | None = Field(default=None)
    sales_preferred_tone: SalesTone | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class IntegrationAccount(SQLModel, table=True):
    """A provider-neutral, workspace-owned inbound integration identity."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    provider: str = Field(max_length=100, index=True)
    external_account_id: str | None = Field(default=None, max_length=255)
    # A provider-neutral identifier resolved by the configured secret backend.
    # It is intentionally not the secret value itself.
    secret_reference: str | None = Field(default=None, max_length=255)
    credential_hash: str = Field(
        sa_column=Column(String(64), unique=True, index=True, nullable=False)
    )
    active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class IntegrationAccountAuditEvent(SQLModel, table=True):
    """Safe, workspace-scoped history for integration account lifecycle events."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    integration_account_id: UUID = Field(
        foreign_key="integrationaccount.id",
        index=True,
    )
    action: IntegrationAccountAuditAction = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)


class InboundIntegrationEventReceipt(SQLModel, table=True):
    """Provider-neutral, durable retry receipt for one authenticated inbound event."""

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "integration_account_id",
            "external_event_id",
            name="uq_inbound_integration_event_receipt",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    integration_account_id: UUID = Field(
        foreign_key="integrationaccount.id",
        index=True,
    )
    external_event_id: str = Field(max_length=200, index=True)
    # Opaque server-generated execution reference. It contains no provider data.
    correlation_id: UUID = Field(default_factory=uuid4, unique=True, index=True)
    created_at: datetime = Field(default_factory=utc_now)


class AIInvocationUsage(SQLModel, table=True):
    """Workspace-scoped usage metadata without prompts, responses, or secrets."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    # Conversations are currently represented by lead-owned message history,
    # not a standalone table, so this remains an optional opaque reference.
    conversation_id: UUID | None = Field(default=None, index=True)
    task_identifier: str = Field(max_length=100, index=True)
    agent_identifier: str = Field(max_length=100, index=True)
    provider: str = Field(max_length=100, index=True)
    model: str = Field(max_length=200, index=True)
    # Null means that the provider did not make that usage value available.
    # It is deliberately distinct from a provider-reported zero.
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 8), nullable=True),
    )
    status: AIInvocationStatus = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)

    @property
    def pricing_known(self) -> bool:
        """A null cost explicitly means no safe cost estimate is available."""

        return self.estimated_cost is not None


class OutboundIntegrationAction(SQLModel, table=True):
    """Provider-neutral outbound delivery intent and its safe outcome."""

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "integration_account_id",
            "idempotency_key",
            name="uq_outbound_integration_action_idempotency",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    integration_account_id: UUID = Field(
        foreign_key="integrationaccount.id",
        index=True,
    )
    external_target_id: str = Field(max_length=255, index=True)
    action_type: OutboundIntegrationActionType = Field(index=True)
    content: str = Field(sa_column=Column(Text, nullable=False))
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    correlation_id: str | None = Field(default=None, max_length=200, index=True)
    idempotency_key: str = Field(max_length=200)
    requires_approval: bool = Field(default=False, index=True)
    approval_request_id: UUID | None = Field(
        default=None,
        foreign_key="approvalrequest.id",
        index=True,
    )
    status: OutboundIntegrationActionStatus = Field(
        default=OutboundIntegrationActionStatus.PENDING,
        index=True,
    )
    priority: OutboundActionPriority = Field(default=OutboundActionPriority.NORMAL, index=True)
    # Opaque future-operator reference. Identity validation is intentionally deferred.
    owner_reference: str | None = Field(default=None, max_length=200, index=True)
    archived_at: datetime | None = Field(default=None, index=True)
    provider_delivery_id: str | None = Field(default=None, max_length=255)
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    not_before: datetime | None = Field(default=None, index=True)
    expires_at: datetime | None = Field(default=None, index=True)
    expired_at: datetime | None = None
    failure_code: str | None = Field(default=None, max_length=100)
    failure_message: str | None = Field(default=None, max_length=500)
    failure_classification: OutboundDeliveryFailureClassification | None = Field(
        default=None,
        index=True,
    )
    created_at: datetime = Field(default_factory=utc_now)


class OutboundIntegrationAuditEvent(SQLModel, table=True):
    """Safe immutable history for outbound action lifecycle transitions."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    integration_account_id: UUID = Field(foreign_key="integrationaccount.id", index=True)
    outbound_integration_action_id: UUID = Field(
        foreign_key="outboundintegrationaction.id", index=True
    )
    action: OutboundIntegrationAuditAction = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)


class OutboundActionAnnotation(SQLModel, table=True):
    """Safe operator-authored note; never participates in delivery state."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    outbound_integration_action_id: UUID = Field(
        foreign_key="outboundintegrationaction.id", index=True
    )
    text: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class OutboundActionLabel(SQLModel, table=True):
    """Safe, normalized operator label for one workspace-scoped outbound action."""

    __table_args__ = (
        UniqueConstraint(
            "outbound_integration_action_id",
            "label",
            name="uq_outbound_action_label",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    outbound_integration_action_id: UUID = Field(
        foreign_key="outboundintegrationaction.id", index=True
    )
    label: str = Field(max_length=64, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class OutboundIntegrationDeliveryAttempt(SQLModel, table=True):
    """Safe, workspace-scoped history of explicit outbound delivery attempts."""

    __table_args__ = (
        UniqueConstraint(
            "outbound_integration_action_id",
            "attempt_number",
            name="uq_outbound_delivery_attempt_number",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    integration_account_id: UUID = Field(
        foreign_key="integrationaccount.id",
        index=True,
    )
    outbound_integration_action_id: UUID = Field(
        foreign_key="outboundintegrationaction.id",
        index=True,
    )
    attempt_number: int = Field(ge=1)
    status: OutboundIntegrationActionStatus = Field(index=True)
    provider_delivery_id: str | None = Field(default=None, max_length=255)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    failure_code: str | None = Field(default=None, max_length=100)
    failure_message: str | None = Field(default=None, max_length=500)
    failure_classification: OutboundDeliveryFailureClassification | None = Field(
        default=None,
        index=True,
    )


class Lead(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(default="demo", index=True, max_length=100)
    full_name: str = Field(index=True, max_length=200)
    company_name: str = Field(index=True, max_length=200)
    job_title: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, index=True, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    website: str | None = Field(default=None, max_length=500)
    source: str = Field(default="manual", max_length=100)
    status: LeadStatus = Field(default=LeadStatus.NEW, index=True)
    score: int = Field(default=0, ge=0, le=100)
    notes: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Product(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str = Field(default="demo", index=True, max_length=100)
    name: str = Field(index=True, max_length=200)
    description: str = Field(sa_column=Column(Text))
    price: float | None = Field(default=None, ge=0)
    minimum_price: float | None = Field(default=None, ge=0)
    active: bool = Field(default=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class LeadResearch(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    lead_id: UUID = Field(foreign_key="lead.id", index=True)
    summary: str = Field(sa_column=Column(Text))
    pain_points: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    opportunities: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    evidence: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)


class ConversationMessage(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    lead_id: UUID = Field(foreign_key="lead.id", index=True)
    direction: str = Field(max_length=20)  # inbound or outbound
    channel: str = Field(default="console", max_length=50)
    stage: SalesStage = Field(default=SalesStage.INTRODUCTION)
    content: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now)


class SalesConversationHandoff(SQLModel, table=True):
    """Historical, workspace-scoped lifecycle record for a Sales handoff."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspace.id", index=True)
    lead_id: UUID = Field(foreign_key="lead.id", index=True)
    reason_code: SalesHandoffReasonCode = Field(index=True)
    explanation: str = Field(max_length=500)
    status: SalesConversationHandoffStatus = Field(
        default=SalesConversationHandoffStatus.ACTIVE,
        index=True,
    )
    created_at: datetime = Field(default_factory=utc_now, index=True)
    resolved_at: datetime | None = Field(default=None, index=True)


class ApprovalRequest(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    lead_id: UUID | None = Field(default=None, foreign_key="lead.id", index=True)
    action_type: str = Field(default="send_message", max_length=100)
    channel: str = Field(default="console", max_length=50)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING, index=True)
    reviewer_note: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None


class FollowUpTask(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    lead_id: UUID = Field(foreign_key="lead.id", index=True)
    due_at: datetime = Field(index=True)
    reason: str = Field(max_length=300)
    status: str = Field(default="pending", max_length=30, index=True)
    created_at: datetime = Field(default_factory=utc_now)
