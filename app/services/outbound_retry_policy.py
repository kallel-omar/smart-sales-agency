"""Provider-neutral eligibility rules for explicit outbound delivery retries."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.config import Settings
from app.models import (
    OutboundDeliveryFailureClassification,
    OutboundIntegrationActionStatus,
)

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
        non_retryable_failure_classes: Iterable[OutboundDeliveryFailureClassification | str] = (),
    ) -> None:
        if not 1 <= maximum_attempts <= 100:
            raise OutboundDeliveryRetryPolicyConfigurationError(
                "Maximum delivery attempts must be between 1 and 100"
            )
        self.maximum_attempts = maximum_attempts
        self.non_retryable_failure_codes = frozenset(
            self._normalize_failure_code(code) for code in non_retryable_failure_codes
        )
        self.non_retryable_failure_classes = frozenset(
            self._normalize_failure_classification(value)
            for value in non_retryable_failure_classes
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "OutboundDeliveryRetryPolicy":
        codes = (
            settings.outbound_delivery_non_retryable_failure_codes.split(",")
            if settings.outbound_delivery_non_retryable_failure_codes
            else ()
        )
        classes = (
            settings.outbound_delivery_non_retryable_failure_classes.split(",")
            if settings.outbound_delivery_non_retryable_failure_classes
            else ()
        )
        return cls(settings.outbound_delivery_max_attempts, codes, classes)

    def evaluate(
        self,
        *,
        attempt_count: int,
        failure_code: str | None,
        failure_classification: OutboundDeliveryFailureClassification | None = None,
    ) -> OutboundDeliveryRetryEligibility:
        """Evaluate an already-failed action before a new attempt is created."""
        if attempt_count >= self.maximum_attempts:
            return OutboundDeliveryRetryEligibility(
                allowed=False,
                denial_reason="maximum_attempts_reached",
            )
        if (
            failure_classification
            == OutboundDeliveryFailureClassification.AUTHENTICATION
        ):
            return OutboundDeliveryRetryEligibility(
                allowed=False,
                denial_reason="authentication_failure_requires_reconnect",
            )
        if (
            failure_code is not None
            and self._normalize_failure_code(failure_code) in self.non_retryable_failure_codes
        ):
            return OutboundDeliveryRetryEligibility(
                allowed=False,
                denial_reason="failure_code_not_retryable",
            )
        if (
            failure_classification is not None
            and failure_classification in self.non_retryable_failure_classes
        ):
            return OutboundDeliveryRetryEligibility(
                allowed=False,
                denial_reason="failure_classification_not_retryable",
            )
        return OutboundDeliveryRetryEligibility(allowed=True)

    def evaluate_action(
        self,
        *,
        action_status: OutboundIntegrationActionStatus,
        attempt_count: int,
        failure_code: str | None,
        failure_classification: OutboundDeliveryFailureClassification | None = None,
    ) -> OutboundDeliveryRetryEligibility:
        """Evaluate retry eligibility for an action without changing its state.

        Only failed actions enter the attempt/failure-code policy. Pending actions
        have not failed, and delivered actions are terminal by design.
        """
        if action_status == OutboundIntegrationActionStatus.DELIVERED:
            return OutboundDeliveryRetryEligibility(
                allowed=False,
                denial_reason="action_delivered",
            )
        if action_status != OutboundIntegrationActionStatus.FAILED:
            return OutboundDeliveryRetryEligibility(
                allowed=False,
                denial_reason="action_not_failed",
            )
        return self.evaluate(
            attempt_count=attempt_count,
            failure_code=failure_code,
            failure_classification=failure_classification,
        )

    @staticmethod
    def _normalize_failure_code(value: str) -> str:
        normalized = value.strip().lower()
        if not _FAILURE_CODE_PATTERN.fullmatch(normalized):
            raise OutboundDeliveryRetryPolicyConfigurationError(
                "Retry-policy failure codes must use lowercase letters, numbers, or underscores"
            )
        return normalized

    @staticmethod
    def _normalize_failure_classification(
        value: OutboundDeliveryFailureClassification | str,
    ) -> OutboundDeliveryFailureClassification:
        try:
            return OutboundDeliveryFailureClassification(str(value).strip().lower())
        except ValueError as exc:
            raise OutboundDeliveryRetryPolicyConfigurationError(
                "Retry-policy failure classes must be provider-neutral values"
            ) from exc
