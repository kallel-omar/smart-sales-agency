from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.services.outbound_retry_delay_policy import (
    OutboundDeliveryRetryDelayPolicy,
    OutboundDeliveryRetryDelayPolicyConfigurationError,
)


def test_fixed_retry_delay_calculates_a_deterministic_next_time():
    policy = OutboundDeliveryRetryDelayPolicy("fixed", 60, 600)
    failed_at = datetime(2026, 8, 9, 12, tzinfo=UTC)
    assert policy.next_retry_at(failed_at, 3) == datetime(2026, 8, 9, 12, 1, tzinfo=UTC)


def test_exponential_delay_is_bounded_and_never_schedules_work():
    policy = OutboundDeliveryRetryDelayPolicy("exponential", 60, 180)
    failed_at = datetime(2026, 8, 9, 12, tzinfo=UTC)
    assert policy.next_retry_at(failed_at, 1) == datetime(2026, 8, 9, 12, 1, tzinfo=UTC)
    assert policy.next_retry_at(failed_at, 4) == datetime(2026, 8, 9, 12, 3, tzinfo=UTC)
    assert policy.next_retry_at(None, 4) is None


def test_delay_configuration_is_validated_and_loaded_from_settings():
    policy = OutboundDeliveryRetryDelayPolicy.from_settings(
        Settings(
            outbound_delivery_retry_delay_strategy="exponential",
            outbound_delivery_retry_delay_seconds=10,
            outbound_delivery_retry_delay_max_seconds=20,
        )
    )
    assert policy.strategy == "exponential"
    with pytest.raises(OutboundDeliveryRetryDelayPolicyConfigurationError):
        OutboundDeliveryRetryDelayPolicy("fixed", 10, 5)
