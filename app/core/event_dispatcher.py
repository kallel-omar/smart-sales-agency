from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable

from app.core.events import BusinessEvent


EventHandler = Callable[[BusinessEvent], Awaitable[None]]


class DuplicateEventHandlerError(ValueError):
    """Raised when the same handler is registered twice for one event type."""


class InMemoryEventDispatcher:
    """
    Dispatches business events to registered asynchronous handlers.

    This implementation is intentionally in-memory for the current modular
    monolith. It can later be replaced by an outbox, queue, or message broker
    without changing business-event contracts.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
    ) -> None:
        """Register a handler for an exact event type."""

        normalized_event_type = event_type.strip()

        if not normalized_event_type:
            raise ValueError("event_type must not be empty")

        handlers = self._handlers[normalized_event_type]

        if handler in handlers:
            raise DuplicateEventHandlerError(
                f"Handler is already registered for '{normalized_event_type}'"
            )

        handlers.append(handler)

    def unsubscribe(
        self,
        event_type: str,
        handler: EventHandler,
    ) -> bool:
        """
        Remove a handler.

        Returns True when the handler was removed and False when it was not
        registered.
        """

        normalized_event_type = event_type.strip()
        handlers = self._handlers.get(normalized_event_type)

        if not handlers or handler not in handlers:
            return False

        handlers.remove(handler)

        if not handlers:
            del self._handlers[normalized_event_type]

        return True

    def handler_count(self, event_type: str) -> int:
        """Return the number of handlers registered for an event type."""

        return len(self._handlers.get(event_type.strip(), ()))

    async def publish(self, event: BusinessEvent) -> int:
        """
        Publish an event to its registered handlers.

        Handlers execute sequentially in registration order. Exceptions are
        propagated so workflow execution can record and retry failures.

        Returns the number of handlers that completed.
        """

        handlers = tuple(self._handlers.get(event.event_type, ()))

        for handler in handlers:
            await handler(event)

        return len(handlers)