from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class Department(StrEnum):
    """Top-level orchestration owners within the platform."""

    BUSINESS = "business"
    SALES = "sales"
    MARKETING = "marketing"
    BACK_OFFICE = "back_office"
    PLATFORM = "platform"


class EventPriority(StrEnum):
    """Business priority used for execution ordering."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(StrEnum):
    """Risk classification used by policy and approval services."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BusinessEvent(BaseModel):
    """
    Provider-independent envelope used for communication between
    supervisors, departments, workflows, and platform services.

    Event-specific payload models will be introduced separately.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    event_id: UUID = Field(default_factory=uuid4)

    workspace_id: UUID

    correlation_id: UUID = Field(
        default_factory=uuid4,
        description=(
            "Connects every event belonging to the same complete "
            "business operation."
        ),
    )

    causation_id: UUID | None = Field(
        default=None,
        description="Identifies the event that caused this event.",
    )

    execution_id: UUID = Field(
        default_factory=uuid4,
        description="Identifies the workflow or task execution.",
    )

    event_type: str = Field(
        min_length=1,
        max_length=120,
        description="Stable event name such as lead.generated.",
    )

    schema_version: str = Field(
        default="1.0",
        pattern=r"^\d+\.\d+$",
    )

    source_department: Department
    destination_department: Department

    priority: EventPriority = EventPriority.NORMAL
    risk_level: RiskLevel = RiskLevel.LOW

    payload: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=utc_now)