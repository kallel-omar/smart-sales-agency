from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.event_factory import create_business_event
from app.core.event_payloads import (
    LeadGeneratedPayload,
    LeadQualifiedPayload,
)
from app.core.events import (
    Department,
    EventPriority,
    RiskLevel,
)


def test_lead_generated_payload_accepts_valid_data() -> None:
    payload = LeadGeneratedPayload(
        lead_id="lead-123",
        source="website",
        customer_name="Example Customer",
        customer_email="customer@example.com",
    )

    assert payload.lead_id == "lead-123"
    assert payload.source == "website"


def test_payload_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LeadGeneratedPayload(
            lead_id="lead-123",
            source="website",
            unknown_field="not-allowed",
        )


def test_payload_is_immutable() -> None:
    payload = LeadGeneratedPayload(
        lead_id="lead-123",
        source="website",
    )

    with pytest.raises(ValidationError):
        payload.source = "facebook"


def test_qualification_score_must_be_between_zero_and_one_hundred() -> None:
    with pytest.raises(ValidationError):
        LeadQualifiedPayload(
            lead_id="lead-123",
            qualification_score=101,
            qualification_status="qualified",
            reason="Score is outside the supported range.",
        )


def test_event_factory_serializes_typed_payload() -> None:
    workspace_id = uuid4()

    payload = LeadQualifiedPayload(
        lead_id="lead-123",
        qualification_score=85,
        qualification_status="qualified",
        reason="The lead has a clear need and sufficient interest.",
    )

    event = create_business_event(
        workspace_id=workspace_id,
        event_type="lead.qualified",
        source_department=Department.SALES,
        destination_department=Department.MARKETING,
        payload=payload,
        priority=EventPriority.HIGH,
        risk_level=RiskLevel.MEDIUM,
    )

    assert event.workspace_id == workspace_id
    assert event.event_type == "lead.qualified"
    assert event.payload["lead_id"] == "lead-123"
    assert event.payload["qualification_score"] == 85
    assert event.priority == EventPriority.HIGH
    assert event.risk_level == RiskLevel.MEDIUM


def test_event_factory_preserves_trace_identifiers() -> None:
    correlation_id = uuid4()
    causation_id = uuid4()
    execution_id = uuid4()

    payload = LeadGeneratedPayload(
        lead_id="lead-456",
        source="instagram",
    )

    event = create_business_event(
        workspace_id=uuid4(),
        event_type="lead.generated",
        source_department=Department.PLATFORM,
        destination_department=Department.SALES,
        payload=payload,
        correlation_id=correlation_id,
        causation_id=causation_id,
        execution_id=execution_id,
    )

    assert event.correlation_id == correlation_id
    assert event.causation_id == causation_id
    assert event.execution_id == execution_id