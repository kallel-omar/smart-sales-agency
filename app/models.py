from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, String, Text, UniqueConstraint
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
