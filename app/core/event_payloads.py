from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EventPayload(BaseModel):
    """Base class for all typed business-event payloads."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )


class LeadGeneratedPayload(EventPayload):
    """Payload emitted when a new lead enters the sales system."""

    lead_id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=100)

    customer_name: str | None = Field(
        default=None,
        max_length=200,
    )

    customer_email: str | None = Field(
        default=None,
        max_length=320,
    )


class LeadQualifiedPayload(EventPayload):
    """Payload emitted after the qualification workflow completes."""

    lead_id: str = Field(min_length=1, max_length=100)

    qualification_score: int = Field(
        ge=0,
        le=100,
    )

    qualification_status: Literal[
        "qualified",
        "unqualified",
        "needs_review",
    ]

    reason: str = Field(
        min_length=1,
        max_length=1000,
    )


class InboundSalesMessagePayload(EventPayload):
    """Normalized inbound customer message for the Sales Department."""

    lead_id: str = Field(min_length=1, max_length=100)
    channel: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=10_000)
    external_event_id: str | None = Field(default=None, max_length=200)
