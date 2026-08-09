"""Deterministic, provider-neutral timing calculations for explicit retries."""

from datetime import datetime, timedelta
from typing import Literal

from app.config import Settings


class OutboundDeliveryRetryDelayPolicyConfigurationError(ValueError):
    """Raised when retry-delay settings are internally inconsistent."""


class OutboundDeliveryRetryDelayPolicy:
    """Calculate retry eligibility time only; never schedule or execute retries."""

    def __init__(
        self,
        strategy: Literal["fixed", "exponential"],
        delay_seconds: int,
        maximum_delay_seconds: int,
    ) -> None:
        if delay_seconds < 0 or maximum_delay_seconds < 0:
            raise OutboundDeliveryRetryDelayPolicyConfigurationError(
                "Retry delays must be non-negative"
            )
        if maximum_delay_seconds < delay_seconds:
            raise OutboundDeliveryRetryDelayPolicyConfigurationError(
                "Maximum retry delay must be greater than or equal to retry delay"
            )
        self.strategy = strategy
        self.delay_seconds = delay_seconds
        self.maximum_delay_seconds = maximum_delay_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> "OutboundDeliveryRetryDelayPolicy":
        return cls(
            settings.outbound_delivery_retry_delay_strategy,
            settings.outbound_delivery_retry_delay_seconds,
            settings.outbound_delivery_retry_delay_max_seconds,
        )

    def next_retry_at(self, failed_at: datetime | None, attempt_count: int) -> datetime | None:
        """Return the earliest retry time for a failed action, or no time if absent."""
        if failed_at is None:
            return None
        multiplier = 1 if self.strategy == "fixed" else 2 ** max(attempt_count - 1, 0)
        seconds = min(self.delay_seconds * multiplier, self.maximum_delay_seconds)
        return failed_at + timedelta(seconds=seconds)
