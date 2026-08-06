import asyncio
from uuid import uuid4

import pytest

from app.core.event_dispatcher import (
    DuplicateEventHandlerError,
    InMemoryEventDispatcher,
)
from app.core.event_factory import create_business_event
from app.core.event_payloads import LeadGeneratedPayload
from app.core.events import BusinessEvent, Department


def create_lead_generated_event() -> BusinessEvent:
    payload = LeadGeneratedPayload(
        lead_id="lead-123",
        source="website",
    )

    return create_business_event(
        workspace_id=uuid4(),
        event_type="lead.generated",
        source_department=Department.PLATFORM,
        destination_department=Department.SALES,
        payload=payload,
    )


def test_dispatcher_calls_registered_handler() -> None:
    dispatcher = InMemoryEventDispatcher()
    received_events: list[BusinessEvent] = []

    async def handler(event: BusinessEvent) -> None:
        received_events.append(event)

    dispatcher.subscribe("lead.generated", handler)

    event = create_lead_generated_event()
    handled_count = asyncio.run(dispatcher.publish(event))

    assert handled_count == 1
    assert received_events == [event]


def test_dispatcher_calls_handlers_in_registration_order() -> None:
    dispatcher = InMemoryEventDispatcher()
    execution_order: list[str] = []

    async def first_handler(event: BusinessEvent) -> None:
        execution_order.append("first")

    async def second_handler(event: BusinessEvent) -> None:
        execution_order.append("second")

    dispatcher.subscribe("lead.generated", first_handler)
    dispatcher.subscribe("lead.generated", second_handler)

    handled_count = asyncio.run(
        dispatcher.publish(create_lead_generated_event())
    )

    assert handled_count == 2
    assert execution_order == ["first", "second"]


def test_dispatcher_ignores_unregistered_event_type() -> None:
    dispatcher = InMemoryEventDispatcher()

    handled_count = asyncio.run(
        dispatcher.publish(create_lead_generated_event())
    )

    assert handled_count == 0


def test_dispatcher_rejects_duplicate_handler_registration() -> None:
    dispatcher = InMemoryEventDispatcher()

    async def handler(event: BusinessEvent) -> None:
        return None

    dispatcher.subscribe("lead.generated", handler)

    with pytest.raises(DuplicateEventHandlerError):
        dispatcher.subscribe("lead.generated", handler)


def test_dispatcher_can_unsubscribe_handler() -> None:
    dispatcher = InMemoryEventDispatcher()
    received_events: list[BusinessEvent] = []

    async def handler(event: BusinessEvent) -> None:
        received_events.append(event)

    dispatcher.subscribe("lead.generated", handler)

    assert dispatcher.handler_count("lead.generated") == 1
    assert dispatcher.unsubscribe("lead.generated", handler) is True
    assert dispatcher.handler_count("lead.generated") == 0
    assert dispatcher.unsubscribe("lead.generated", handler) is False

    handled_count = asyncio.run(
        dispatcher.publish(create_lead_generated_event())
    )

    assert handled_count == 0
    assert received_events == []