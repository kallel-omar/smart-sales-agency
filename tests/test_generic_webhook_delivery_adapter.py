import hmac
import json
from hashlib import sha256

import httpx

from app.models import IntegrationAccount, OutboundIntegrationAction, OutboundIntegrationActionType
from app.services.delivery_adapters import (
    GenericWebhookDeliveryAdapter,
    WebhookHttpResponse,
    normalize_webhook_response,
)


class RecordingTransport:
    def __init__(self, response: WebhookHttpResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url, *, content, headers, timeout):
        self.calls.append({"url": url, "content": content, "headers": headers, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _action() -> OutboundIntegrationAction:
    return OutboundIntegrationAction(
        external_target_id="recipient",
        action_type=OutboundIntegrationActionType.SEND_MESSAGE,
        content="hello",
        idempotency_key="private-key",
    )


def test_generic_webhook_adapter_posts_safe_serialized_action():
    transport = RecordingTransport(WebhookHttpResponse(202, {"x-delivery-id": "accepted-1"}))
    adapter = GenericWebhookDeliveryAdapter("https://example.test/deliver", transport=transport)
    action = _action()
    account = IntegrationAccount(provider="generic_webhook")
    result = adapter.deliver(action, account)

    assert result.delivered is True
    assert result.provider_delivery_id == "accepted-1"
    request = transport.calls[0]
    assert request["url"] == "https://example.test/deliver"
    assert request["headers"] == {"Content-Type": "application/json", "X-Webhook-Signing": "none"}
    assert json.loads(request["content"]) == {
        "provider": "generic_webhook",
        "integration_account_id": str(account.id),
        "external_account_id": None,
        "action_id": str(action.id),
        "action_type": "send_message",
        "external_target_id": "recipient",
        "content": "hello",
    }
    assert b"private-key" not in request["content"]


def test_whatsapp_cloud_adapter_posts_safe_account_context_without_credentials():
    transport = RecordingTransport(
        WebhookHttpResponse(202, {"x-delivery-id": "wamid.outbound-message-id"})
    )
    adapter = GenericWebhookDeliveryAdapter("https://n8n.test/webhook/whatsapp", transport=transport)
    action = _action()
    account = IntegrationAccount(
        provider="whatsapp_cloud",
        external_account_id="555666777888999",
        secret_reference="INTEGRATION_SECRET_WHATSAPP_CLOUD",
    )

    result = adapter.deliver(action, account)

    assert result.delivered is True
    assert result.provider_delivery_id == "wamid.outbound-message-id"
    body = json.loads(transport.calls[0]["content"])
    assert body == {
        "provider": "whatsapp_cloud",
        "integration_account_id": str(account.id),
        "external_account_id": "555666777888999",
        "action_id": str(action.id),
        "action_type": "send_message",
        "external_target_id": "recipient",
        "content": "hello",
    }
    serialized = json.dumps(body).lower()
    assert "access_token" not in serialized
    assert "authorization" not in serialized
    assert "secret" not in serialized


def test_generic_webhook_adapter_handles_missing_configuration_and_network_failures_safely():
    missing = GenericWebhookDeliveryAdapter("", transport=RecordingTransport(WebhookHttpResponse(200, {})))
    assert missing.deliver(_action(), IntegrationAccount(provider="generic_webhook")).failure_code == "webhook_configuration_missing"

    network = GenericWebhookDeliveryAdapter(
        "https://example.test/deliver",
        transport=RecordingTransport(httpx.ConnectError("network")),
    )
    result = network.deliver(_action(), IntegrationAccount(provider="generic_webhook"))
    assert result.failure_code == "webhook_network_error"
    assert result.failure_classification.value == "temporary"


def test_generic_webhook_adapter_signs_raw_serialized_bytes_when_configured():
    transport = RecordingTransport(WebhookHttpResponse(200, {}))
    adapter = GenericWebhookDeliveryAdapter(
        "https://example.test/deliver",
        transport=transport,
        signing_enabled=True,
        secret_resolver=StaticSecretResolver("signing-secret"),
        timestamp_provider=lambda: 1_700_000_000,
    )
    adapter.deliver(_action(), IntegrationAccount(provider="generic_webhook", secret_reference="INTEGRATION_SECRET_TEST"))

    headers = transport.calls[0]["headers"]
    assert headers["X-Webhook-Signing"] == "hmac-sha256"
    assert headers["X-Webhook-Timestamp"] == "1700000000"
    assert headers["X-Webhook-Signature"] == hmac.new(
        b"signing-secret",
        b"1700000000." + transport.calls[0]["content"],
        sha256,
    ).hexdigest()
    assert "signing-secret" not in str(headers)


def test_signing_enabled_without_a_resolved_secret_fails_before_network_io():
    transport = RecordingTransport(WebhookHttpResponse(200, {}))
    adapter = GenericWebhookDeliveryAdapter(
        "https://example.test/deliver",
        transport=transport,
        signing_enabled=True,
        secret_resolver=StaticSecretResolver(None),
    )
    result = adapter.deliver(_action(), IntegrationAccount(provider="generic_webhook"))
    assert result.failure_code == "webhook_signing_secret_unavailable"
    assert transport.calls == []


def test_generic_webhook_response_normalization_uses_safe_http_semantics():
    cases = [
        (200, True, None),
        (429, False, "rate_limit"),
        (401, False, "authentication"),
        (403, False, "authentication"),
        (422, False, "validation"),
        (503, False, "temporary"),
        (302, False, "unknown"),
    ]
    for status_code, delivered, classification in cases:
        result = normalize_webhook_response(WebhookHttpResponse(status_code, {}))
        assert result.delivered is delivered
        assert (
            result.failure_classification.value if result.failure_classification else None
        ) == classification
        assert "body" not in result.__dict__


def test_whatsapp_cloud_provider_success_and_failure_map_through_webhook_boundary():
    success = normalize_webhook_response(
        WebhookHttpResponse(202, {"x-delivery-id": "wamid.meta-outbound-id"})
    )
    assert success.delivered is True
    assert success.provider_delivery_id == "wamid.meta-outbound-id"

    missing_token = normalize_webhook_response(WebhookHttpResponse(503, {}))
    assert missing_token.delivered is False
    assert missing_token.failure_code == "webhook_server_error"
    assert missing_token.failure_classification.value == "temporary"


class StaticSecretResolver:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def resolve(self, reference: str | None) -> str | None:
        del reference
        return self.value
