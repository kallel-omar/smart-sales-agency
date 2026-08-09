from __future__ import annotations

from enum import StrEnum

from app.core.event_payloads import (
    EventPayload,
    InboundSalesMessagePayload,
    LeadGeneratedPayload,
    LeadQualifiedPayload,
)


class EventType(StrEnum):
    """Supported core business-event names."""

    LEAD_GENERATED = "lead.generated"
    LEAD_QUALIFIED = "lead.qualified"
    SALES_INBOUND_MESSAGE = "sales.inbound_message"


class UnsupportedEventTypeError(ValueError):
    """Raised when an event type is not registered."""


class EventPayloadMismatchError(TypeError):
    """Raised when an event uses the wrong payload model."""


EVENT_PAYLOAD_TYPES: dict[EventType, type[EventPayload]] = {
    EventType.LEAD_GENERATED: LeadGeneratedPayload,
    EventType.LEAD_QUALIFIED: LeadQualifiedPayload,
    EventType.SALES_INBOUND_MESSAGE: InboundSalesMessagePayload,
}


def normalize_event_type(event_type: EventType | str) -> EventType:
    """Convert a string or enum member into a supported EventType."""

    try:
        return EventType(event_type)
    except ValueError as exc:
        raise UnsupportedEventTypeError(
            f"Unsupported business event type: '{event_type}'"
        ) from exc


def validate_event_payload(
    event_type: EventType | str,
    payload: EventPayload,
) -> EventType:
    """
    Validate that a registered event type uses its expected payload model.

    Returns the normalized event type when validation succeeds.
    """

    normalized_event_type = normalize_event_type(event_type)
    expected_payload_type = EVENT_PAYLOAD_TYPES[normalized_event_type]

    if not isinstance(payload, expected_payload_type):
        raise EventPayloadMismatchError(
            f"Event '{normalized_event_type.value}' requires "
            f"{expected_payload_type.__name__}, "
            f"but received {type(payload).__name__}"
        )

    return normalized_event_type
