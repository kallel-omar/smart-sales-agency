import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import OutboundDeliveryFailureClassification
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy


def test_temporary_and_rate_limit_failures_remain_retryable_by_default():
    policy = OutboundDeliveryRetryPolicy(3)
    for classification in (
        OutboundDeliveryFailureClassification.TEMPORARY,
        OutboundDeliveryFailureClassification.RATE_LIMIT,
    ):
        assert policy.evaluate(
            attempt_count=1,
            failure_code="safe_code",
            failure_classification=classification,
        ).allowed is True


def test_configured_failure_classes_deny_retries_deterministically():
    policy = OutboundDeliveryRetryPolicy(
        3,
        non_retryable_failure_classes=("permanent", "authentication", "validation"),
    )
    expected_reasons = {
        OutboundDeliveryFailureClassification.PERMANENT: "failure_classification_not_retryable",
        OutboundDeliveryFailureClassification.AUTHENTICATION: (
            "authentication_failure_requires_reconnect"
        ),
        OutboundDeliveryFailureClassification.VALIDATION: "failure_classification_not_retryable",
    }
    for classification, expected_reason in expected_reasons.items():
        result = policy.evaluate(
            attempt_count=1,
            failure_code="safe_code",
            failure_classification=classification,
        )
        assert result.allowed is False
        assert result.denial_reason == expected_reason


def test_failure_class_configuration_is_validated_and_loaded_from_settings():
    settings = Settings(outbound_delivery_non_retryable_failure_classes="permanent,validation")
    policy = OutboundDeliveryRetryPolicy.from_settings(settings)
    assert policy.non_retryable_failure_classes == {
        OutboundDeliveryFailureClassification.PERMANENT,
        OutboundDeliveryFailureClassification.VALIDATION,
    }
    with pytest.raises(ValidationError):
        Settings(outbound_delivery_non_retryable_failure_classes="provider_internal")
