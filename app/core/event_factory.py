from __future__ import annotations

from uuid import UUID

from app.core.event_payloads import EventPayload
from app.core.events import (
    BusinessEvent,
    Department,
    EventPriority,
    RiskLevel,
)


def create_business_event(
    *,
    workspace_id: UUID,
    event_type: str,
    source_department: Department,
    destination_department: Department,
    payload: EventPayload,
    correlation_id: UUID | None = None,
    causation_id: UUID | None = None,
    execution_id: UUID | None = None,
    priority: EventPriority = EventPriority.NORMAL,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> BusinessEvent:
    """
    Build a BusinessEvent from a validated typed payload.

    Optional tracing identifiers are only overridden when explicitly supplied.
    """

    event_data = {
        "workspace_id": workspace_id,
        "event_type": event_type,
        "source_department": source_department,
        "destination_department": destination_department,
        "priority": priority,
        "risk_level": risk_level,
        "payload": payload.model_dump(mode="json"),
    }

    if correlation_id is not None:
        event_data["correlation_id"] = correlation_id

    if causation_id is not None:
        event_data["causation_id"] = causation_id

    if execution_id is not None:
        event_data["execution_id"] = execution_id

    return BusinessEvent.model_validate(event_data)