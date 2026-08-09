"""Provider-neutral contracts for outbound delivery adapters."""

import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

import httpx

from app.models import (
    IntegrationAccount,
    OutboundDeliveryFailureClassification,
    OutboundIntegrationAction,
    OutboundIntegrationActionType,
)
from app.services.secret_resolver import EnvironmentSecretResolver, SecretResolver

_GENERIC_FAILURE_CLASSIFICATIONS = {
    "adapter_execution_failed": OutboundDeliveryFailureClassification.TEMPORARY,
    "adapter_not_configured": OutboundDeliveryFailureClassification.PERMANENT,
    "adapter_capabilities_unavailable": OutboundDeliveryFailureClassification.PERMANENT,
    "unsupported_action_type": OutboundDeliveryFailureClassification.VALIDATION,
    "content_too_long": OutboundDeliveryFailureClassification.VALIDATION,
    "authentication_failed": OutboundDeliveryFailureClassification.AUTHENTICATION,
    "rate_limited": OutboundDeliveryFailureClassification.RATE_LIMIT,
    "validation_failed": OutboundDeliveryFailureClassification.VALIDATION,
    "temporary_failure": OutboundDeliveryFailureClassification.TEMPORARY,
    "permanent_failure": OutboundDeliveryFailureClassification.PERMANENT,
}


@dataclass(frozen=True)
class DeliveryAdapterCapabilities:
    """Safe, deterministic constraints declared by a delivery adapter."""

    supported_action_types: frozenset[OutboundIntegrationActionType]
    max_content_length: int | None = None

    def __post_init__(self) -> None:
        if not self.supported_action_types:
            raise ValueError("Delivery adapter must support at least one action type")
        if self.max_content_length is not None and self.max_content_length < 1:
            raise ValueError("Delivery adapter maximum content length must be at least 1")


DEFAULT_DELIVERY_ADAPTER_CAPABILITIES = DeliveryAdapterCapabilities(
    supported_action_types=frozenset({OutboundIntegrationActionType.SEND_MESSAGE}),
)


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


@dataclass(frozen=True)
class WebhookHttpResponse:
    status_code: int
    headers: dict[str, str]


class WebhookHttpTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> WebhookHttpResponse: ...


class HttpxWebhookHttpTransport:
    """Small HTTP boundary that keeps the generic adapter easy to test."""

    def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: httpx.Timeout) -> WebhookHttpResponse:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, content=content, headers=headers)
        return WebhookHttpResponse(status_code=response.status_code, headers=dict(response.headers))


class GenericWebhookDeliveryAdapter:
    """Generic outbound HTTP delivery without provider or workflow semantics."""

    capabilities = DEFAULT_DELIVERY_ADAPTER_CAPABILITIES

    def __init__(
        self,
        endpoint: str,
        *,
        connect_timeout_seconds: float = 5,
        read_timeout_seconds: float = 15,
        transport: WebhookHttpTransport | None = None,
        signing_enabled: bool = False,
        secret_resolver: SecretResolver | None = None,
        timestamp_provider: Callable[[], int] | None = None,
    ) -> None:
        self.endpoint = endpoint.strip()
        self.timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self.transport = transport or HttpxWebhookHttpTransport()
        self.signing_enabled = signing_enabled
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self.timestamp_provider = timestamp_provider or (lambda: int(time.time()))

    def deliver(
        self, action: OutboundIntegrationAction, account: IntegrationAccount
    ) -> DeliveryAdapterResult:
        if not self.endpoint:
            return DeliveryAdapterResult.failure(
                "webhook_configuration_missing",
                "Generic webhook endpoint is not configured",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        body = self._serialize_action(action)
        headers, signing_failure = self._headers(body, account)
        if signing_failure is not None:
            return signing_failure
        try:
            response = self.transport.post(
                self.endpoint,
                content=body,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            return DeliveryAdapterResult.failure(
                "webhook_network_error",
                "Generic webhook delivery failed",
                OutboundDeliveryFailureClassification.TEMPORARY,
            )
        return normalize_webhook_response(response)

    def _headers(
        self, body: bytes, account: IntegrationAccount
    ) -> tuple[dict[str, str], DeliveryAdapterResult | None]:
        headers = {"Content-Type": "application/json"}
        if not self.signing_enabled:
            headers["X-Webhook-Signing"] = "none"
            return headers, None
        secret = self.secret_resolver.resolve(account.secret_reference)
        if not secret:
            return headers, DeliveryAdapterResult.failure(
                "webhook_signing_secret_unavailable",
                "Generic webhook signing secret is unavailable",
                OutboundDeliveryFailureClassification.AUTHENTICATION,
            )
        timestamp = str(self.timestamp_provider())
        signature = hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, sha256
        ).hexdigest()
        headers.update(
            {
                "X-Webhook-Signing": "hmac-sha256",
                "X-Webhook-Timestamp": timestamp,
                "X-Webhook-Signature": signature,
            }
        )
        return headers, None

    @staticmethod
    def _serialize_action(action: OutboundIntegrationAction) -> bytes:
        import json

        return json.dumps(
            {
                "action_id": str(action.id),
                "action_type": action.action_type.value,
                "external_target_id": action.external_target_id,
                "content": action.content,
            },
            separators=(",", ":"),
        ).encode()


def normalize_webhook_response(response: WebhookHttpResponse) -> DeliveryAdapterResult:
    """Map generic HTTP semantics to safe provider-neutral delivery outcomes."""
    status_code = response.status_code
    if 200 <= status_code < 300:
        return DeliveryAdapterResult.success(response.headers.get("x-delivery-id"))
    if status_code == 429:
        return DeliveryAdapterResult.failure(
            "webhook_rate_limited",
            "Generic webhook delivery was rate limited",
            OutboundDeliveryFailureClassification.RATE_LIMIT,
        )
    if status_code in {401, 403}:
        return DeliveryAdapterResult.failure(
            "webhook_authentication_failed",
            "Generic webhook authentication was rejected",
            OutboundDeliveryFailureClassification.AUTHENTICATION,
        )
    if 400 <= status_code < 500:
        return DeliveryAdapterResult.failure(
            "webhook_validation_failed",
            "Generic webhook request was rejected",
            OutboundDeliveryFailureClassification.VALIDATION,
        )
    if 500 <= status_code < 600:
        return DeliveryAdapterResult.failure(
            "webhook_server_error",
            "Generic webhook service failed",
            OutboundDeliveryFailureClassification.TEMPORARY,
        )
    return DeliveryAdapterResult.failure(
        "webhook_response_unknown",
        "Generic webhook delivery returned an unknown response",
        OutboundDeliveryFailureClassification.UNKNOWN,
    )


class DeliveryAdapterRegistry:
    """Explicit mapping from a persisted provider name to an adapter."""

    def __init__(self, adapters: dict[str, DeliveryAdapter]) -> None:
        self._adapters = dict(adapters)

    def get(self, provider: str) -> DeliveryAdapter | None:
        return self._adapters.get(provider)

    def capabilities_for(self, provider: str) -> DeliveryAdapterCapabilities | None:
        """Return a safe declaration, with a compatible generic legacy default."""
        adapter = self.get(provider)
        if adapter is None:
            return None
        capabilities = getattr(adapter, "capabilities", DEFAULT_DELIVERY_ADAPTER_CAPABILITIES)
        if not isinstance(capabilities, DeliveryAdapterCapabilities):
            return None
        return capabilities


class NoopDeliveryAdapter:
    """Safe development adapter that performs no external I/O."""

    capabilities = DEFAULT_DELIVERY_ADAPTER_CAPABILITIES

    def deliver(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> DeliveryAdapterResult:
        del account
        return DeliveryAdapterResult.success(provider_delivery_id=f"noop-{action.id}")


def default_delivery_adapter_registry(
    generic_webhook_adapter: DeliveryAdapter | None = None,
) -> DeliveryAdapterRegistry:
    """Return the intentionally minimal adapter set available in this task."""
    adapters: dict[str, DeliveryAdapter] = {"generic_hmac": NoopDeliveryAdapter()}
    if generic_webhook_adapter is not None:
        adapters["generic_webhook"] = generic_webhook_adapter
    return DeliveryAdapterRegistry(adapters)
