"""Provider-neutral eligibility rules for explicit outbound delivery retries."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.config import Settings

_FAILURE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class OutboundDeliveryRetryPolicyConfigurationError(ValueError):
    """Raised when retry-policy inputs are not safe operational values."""


@dataclass(frozen=True)
class OutboundDeliveryRetryEligibility:
    """A safe, deterministic retry decision without provider-specific detail."""

    allowed: bool
    denial_reason: str | None = None


class OutboundDeliveryRetryPolicy:
    """Limit manual retries by attempt count and safe failure-code classification.

    Failure codes are retryable by default. Operators can explicitly mark generic,
    provider-neutral failure codes as non-retryable through configuration.
    """

    def __init__(
        self,
        maximum_attempts: int,
        non_retryable_failure_codes: Iterable[str] = (),
    ) -> None:
        if not 1 <= maximum_attempts <= 100:
            raise OutboundDeliveryRetryPolicyConfigurationError(
                "Maximum delivery attempts must be between 1 and 100"
            )
        self.maximum_attempts = maximum_attempts
        self.non_retryable_failure_codes = frozenset(
            self._normalize_failure_code(code) for code in non_retryable_failure_codes
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "OutboundDeliveryRetryPolicy":
        codes = (
            settings.outbound_delivery_non_retryable_failure_codes.split(",")
            if settings.outbound_delivery_non_retryable_failure_codes
            else ()
        )
        return cls(settings.outbound_delivery_max_attempts, codes)

    def evaluate(
        self,
        *,
        attempt_count: int,
        failure_code: str | None,
    ) -> OutboundDeliveryRetryEligibility:
        """Evaluate an already-failed action before a new attempt is created."""
        if attempt_count >= self.maximum_attempts:
            return OutboundDeliveryRetryEligibility(
                allowed=False,
                denial_reason="maximum_attempts_reached",
            )
        if (
            failure_code is not None
            and self._normalize_failure_code(failure_code) in self.non_retryable_failure_codes
        ):
            return OutboundDeliveryRetryEligibility(
                allowed=False,
                denial_reason="failure_code_not_retryable",
            )
        return OutboundDeliveryRetryEligibility(allowed=True)

    @staticmethod
    def _normalize_failure_code(value: str) -> str:
        normalized = value.strip().lower()
        if not _FAILURE_CODE_PATTERN.fullmatch(normalized):
            raise OutboundDeliveryRetryPolicyConfigurationError(
                "Retry-policy failure codes must use lowercase letters, numbers, or underscores"
            )
        return normalized
