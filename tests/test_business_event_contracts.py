from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.events import (
    BusinessEvent,
    Department,
    EventPriority,
    RiskLevel,
)


def create_event() -> BusinessEvent:
    return BusinessEvent(
        workspace_id=uuid4(),
        event_type="lead.generated",
        source_department=Department.SALES,
        destination_department=Department.MARKETING,
        payload={"lead_id": str(uuid4())},
    )


def test_business_event_has_safe_defaults() -> None:
    event = create_event()

    assert event.event_id is not None
    assert event.correlation_id is not None
    assert event.execution_id is not None
    assert event.causation_id is None
    assert event.schema_version == "1.0"
    assert event.priority == EventPriority.NORMAL
    assert event.risk_level == RiskLevel.LOW
    assert event.created_at.tzinfo is not None


def test_business_event_is_immutable() -> None:
    event = create_event()

    with pytest.raises(ValidationError):
        event.event_type = "lead.updated"


def test_business_event_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BusinessEvent(
            workspace_id=uuid4(),
            event_type="lead.generated",
            source_department=Department.SALES,
            destination_department=Department.MARKETING,
            unknown_field="not-allowed",
        )


def test_business_event_rejects_invalid_schema_version() -> None:
    with pytest.raises(ValidationError):
        BusinessEvent(
            workspace_id=uuid4(),
            event_type="lead.generated",
            schema_version="version-one",
            source_department=Department.SALES,
            destination_department=Department.MARKETING,
        )