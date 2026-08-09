import json

import httpx

from app.models import IntegrationAccount, OutboundIntegrationAction, OutboundIntegrationActionType
from app.services.delivery_adapters import (
    GenericWebhookDeliveryAdapter,
    WebhookHttpResponse,
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
    result = adapter.deliver(action, IntegrationAccount(provider="generic_webhook"))

    assert result.delivered is True
    assert result.provider_delivery_id == "accepted-1"
    request = transport.calls[0]
    assert request["url"] == "https://example.test/deliver"
    assert request["headers"] == {"Content-Type": "application/json"}
    assert json.loads(request["content"]) == {
        "action_id": str(action.id),
        "action_type": "send_message",
        "external_target_id": "recipient",
        "content": "hello",
    }
    assert b"private-key" not in request["content"]


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
