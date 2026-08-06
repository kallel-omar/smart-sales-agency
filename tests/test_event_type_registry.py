from uuid import uuid4

import pytest

from app.core.event_factory import create_business_event
from app.core.event_payloads import (
    LeadGeneratedPayload,
    LeadQualifiedPayload,
)
from app.core.event_types import (
    EventPayloadMismatchError,
    EventType,
    UnsupportedEventTypeError,
    normalize_event_type,
)
from app.core.events import Department


def test_normalize_event_type_accepts_string_and_enum() -> None:
    assert (
        normalize_event_type("lead.generated")
        == EventType.LEAD_GENERATED
    )

    assert (
        normalize_event_type(EventType.LEAD_QUALIFIED)
        == EventType.LEAD_QUALIFIED
    )


def test_event_factory_rejects_unsupported_event_type() -> None:
    payload = LeadGeneratedPayload(
        lead_id="lead-123",
        source="website",
    )

    with pytest.raises(UnsupportedEventTypeError):
        create_business_event(
            workspace_id=uuid4(),
            event_type="lead.deleted",
            source_department=Department.PLATFORM,
            destination_department=Department.SALES,
            payload=payload,
        )


def test_event_factory_rejects_payload_mismatch() -> None:
    payload = LeadGeneratedPayload(
        lead_id="lead-123",
        source="website",
    )

    with pytest.raises(EventPayloadMismatchError):
        create_business_event(
            workspace_id=uuid4(),
            event_type=EventType.LEAD_QUALIFIED,
            source_department=Department.SALES,
            destination_department=Department.MARKETING,
            payload=payload,
        )


def test_event_factory_accepts_matching_payload() -> None:
    payload = LeadQualifiedPayload(
        lead_id="lead-123",
        qualification_score=90,
        qualification_status="qualified",
        reason="The lead has a confirmed business need.",
    )

    event = create_business_event(
        workspace_id=uuid4(),
        event_type=EventType.LEAD_QUALIFIED,
        source_department=Department.SALES,
        destination_department=Department.MARKETING,
        payload=payload,
    )

    assert event.event_type == "lead.qualified"
    assert event.payload["qualification_score"] == 90