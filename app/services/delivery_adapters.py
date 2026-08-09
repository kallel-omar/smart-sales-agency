"""Provider-neutral contracts for outbound delivery adapters."""

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    IntegrationAccount,
    OutboundDeliveryFailureClassification,
    OutboundIntegrationAction,
)

_GENERIC_FAILURE_CLASSIFICATIONS = {
    "adapter_execution_failed": OutboundDeliveryFailureClassification.TEMPORARY,
    "adapter_not_configured": OutboundDeliveryFailureClassification.PERMANENT,
    "authentication_failed": OutboundDeliveryFailureClassification.AUTHENTICATION,
    "rate_limited": OutboundDeliveryFailureClassification.RATE_LIMIT,
    "validation_failed": OutboundDeliveryFailureClassification.VALIDATION,
    "temporary_failure": OutboundDeliveryFailureClassification.TEMPORARY,
    "permanent_failure": OutboundDeliveryFailureClassification.PERMANENT,
}


@dataclass(frozen=True)
class DeliveryAdapterResult:
    """Safe, provider-neutral result returned after one adapter attempt."""

    delivered: bool
    provider_delivery_id: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    failure_classification: OutboundDeliveryFailureClassification | None = None

    @classmethod
    def success(cls, provider_delivery_id: str | None = None) -> "DeliveryAdapterResult":
        return cls(delivered=True, provider_delivery_id=provider_delivery_id)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        classification: OutboundDeliveryFailureClassification | None = None,
    ) -> "DeliveryAdapterResult":
        return cls(
            delivered=False,
            failure_code=code,
            failure_message=message,
            failure_classification=classification or classify_failure_code(code),
        )


def classify_failure_code(code: str | None) -> OutboundDeliveryFailureClassification:
    """Map generic safe failure codes without introducing provider behavior."""
    if not code:
        return OutboundDeliveryFailureClassification.UNKNOWN
    return _GENERIC_FAILURE_CLASSIFICATIONS.get(
        code.strip().lower(),
        OutboundDeliveryFailureClassification.UNKNOWN,
    )


class DeliveryAdapter(Protocol):
    """An adapter that attempts one persisted provider-neutral action."""

    def deliver(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> DeliveryAdapterResult: ...


class DeliveryAdapterRegistry:
    """Explicit mapping from a persisted provider name to an adapter."""

    def __init__(self, adapters: dict[str, DeliveryAdapter]) -> None:
        self._adapters = dict(adapters)

    def get(self, provider: str) -> DeliveryAdapter | None:
        return self._adapters.get(provider)


class NoopDeliveryAdapter:
    """Safe development adapter that performs no external I/O."""

    def deliver(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> DeliveryAdapterResult:
        del account
        return DeliveryAdapterResult.success(provider_delivery_id=f"noop-{action.id}")


def default_delivery_adapter_registry() -> DeliveryAdapterRegistry:
    """Return the intentionally minimal adapter set available in this task."""
    return DeliveryAdapterRegistry({"generic_hmac": NoopDeliveryAdapter()})
