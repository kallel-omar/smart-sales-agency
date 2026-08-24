from types import SimpleNamespace
from uuid import uuid4

import httpx
from sqlmodel import Session, create_engine

from app.config import Settings
from app.integrations.providers import WHATSAPP_CLOUD_PROVIDER
from app.models import (
    IntegrationAccount,
    OutboundDeliveryFailureClassification,
    OutboundIntegrationAction,
    OutboundIntegrationActionType,
)
from app.services.delivery_adapters import (
    WhatsAppCloudDeliveryAdapter,
    WhatsAppCloudHttpResponse,
    default_delivery_adapter_registry,
)
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceNotFoundError,
)
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


class FakeCredentialReferenceService:
    def __init__(self, secret_reference: str = "INTEGRATION_SECRET_WHATSAPP_API_TOKEN"):
        self.secret_reference = secret_reference

    def get_for_integration_account(self, account, purpose):
        assert purpose == "api_access_token"
        assert account.provider == WHATSAPP_CLOUD_PROVIDER
        return SimpleNamespace(secret_reference=self.secret_reference)


class MissingCredentialReferenceService:
    def get_for_integration_account(self, account, purpose):
        raise IntegrationCredentialReferenceNotFoundError(
            "Integration credential reference not found"
        )


class FakeSecretResolver:
    def __init__(self, value: str | None = "test-whatsapp-access-token"):
        self.value = value
        self.references: list[str | None] = []

    def resolve(self, reference):
        self.references.append(reference)
        return self.value


class FakeWhatsAppTransport:
    def __init__(self, response: WhatsAppCloudHttpResponse):
        self.response = response
        self.calls: list[dict] = []

    def post(self, url, *, payload, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return self.response


def make_account(
    *,
    provider: str = WHATSAPP_CLOUD_PROVIDER,
    external_account_id: str = "123456789012345",
) -> IntegrationAccount:
    return IntegrationAccount(
        workspace_id=uuid4(),
        provider=provider,
        external_account_id=external_account_id,
        credential_hash="a" * 64,
    )


def make_action(
    account: IntegrationAccount,
    *,
    external_target_id: str = "21620123456",
    content: str = "Hello from HIRI",
) -> OutboundIntegrationAction:
    return OutboundIntegrationAction(
        workspace_id=account.workspace_id,
        integration_account_id=account.id,
        external_target_id=external_target_id,
        action_type=OutboundIntegrationActionType.SEND_MESSAGE,
        content=content,
        idempotency_key=f"whatsapp-test-{uuid4()}",
    )


def make_adapter(
    *,
    response: WhatsAppCloudHttpResponse | None = None,
    credential_service=None,
    secret_resolver=None,
):
    transport = FakeWhatsAppTransport(
        response
        or WhatsAppCloudHttpResponse(
            status_code=200,
            headers={},
            body={"messages": [{"id": "wamid.test-message-id"}]},
        )
    )

    adapter = WhatsAppCloudDeliveryAdapter(
        credential_service or FakeCredentialReferenceService(),
        graph_api_base_url="https://graph.facebook.com",
        graph_api_version="v23.0",
        transport=transport,
        secret_resolver=secret_resolver or FakeSecretResolver(),
    )

    return adapter, transport


def test_whatsapp_cloud_adapter_sends_existing_provider_neutral_action():
    account = make_account()
    action = make_action(account)
    adapter, transport = make_adapter()

    result = adapter.deliver(action, account)

    assert result.delivered is True
    assert result.provider_delivery_id == "wamid.test-message-id"

    assert len(transport.calls) == 1
    call = transport.calls[0]

    assert (
        call["url"]
        == "https://graph.facebook.com/v23.0/123456789012345/messages"
    )
    assert call["payload"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "21620123456",
        "type": "text",
        "text": {
            "preview_url": False,
            "body": "Hello from HIRI",
        },
    }
    assert call["headers"]["Authorization"] == "Bearer test-whatsapp-access-token"
    assert call["headers"]["Content-Type"] == "application/json"


def test_whatsapp_cloud_adapter_resolves_api_access_token_reference():
    account = make_account()
    action = make_action(account)
    resolver = FakeSecretResolver()

    adapter, _ = make_adapter(secret_resolver=resolver)

    result = adapter.deliver(action, account)

    assert result.delivered is True
    assert resolver.references == ["INTEGRATION_SECRET_WHATSAPP_API_TOKEN"]


def test_whatsapp_cloud_adapter_rejects_provider_mismatch_without_http_call():
    account = make_account(provider="generic_hmac")
    action = make_action(account)
    adapter, transport = make_adapter()

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "whatsapp_cloud_provider_mismatch"
    assert (
        result.failure_classification
        == OutboundDeliveryFailureClassification.VALIDATION
    )
    assert transport.calls == []


def test_whatsapp_cloud_adapter_requires_phone_number_id():
    account = make_account(external_account_id="")
    action = make_action(account)
    adapter, transport = make_adapter()

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "whatsapp_cloud_phone_number_id_missing"
    assert transport.calls == []


def test_whatsapp_cloud_adapter_requires_access_token_reference():
    account = make_account()
    action = make_action(account)

    adapter, transport = make_adapter(
        credential_service=MissingCredentialReferenceService(),
    )

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "whatsapp_cloud_access_token_reference_missing"
    assert (
        result.failure_classification
        == OutboundDeliveryFailureClassification.AUTHENTICATION
    )
    assert transport.calls == []


def test_whatsapp_cloud_adapter_requires_resolvable_access_token():
    account = make_account()
    action = make_action(account)

    adapter, transport = make_adapter(
        secret_resolver=FakeSecretResolver(None),
    )

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "whatsapp_cloud_access_token_unavailable"
    assert (
        result.failure_classification
        == OutboundDeliveryFailureClassification.AUTHENTICATION
    )
    assert transport.calls == []


def test_whatsapp_cloud_adapter_maps_authentication_failure():
    account = make_account()
    action = make_action(account)

    adapter, _ = make_adapter(
        response=WhatsAppCloudHttpResponse(
            status_code=401,
            headers={},
            body=None,
        )
    )

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "whatsapp_cloud_authentication_failed"
    assert (
        result.failure_classification
        == OutboundDeliveryFailureClassification.AUTHENTICATION
    )


def test_whatsapp_cloud_adapter_maps_permission_denied_separately():
    account = make_account()
    action = make_action(account)

    adapter, _ = make_adapter(
        response=WhatsAppCloudHttpResponse(
            status_code=403,
            headers={"Authorization": "must-not-be-persisted"},
            body={"error": {"message": "sensitive provider response"}},
        )
    )

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "provider_permission_denied"
    assert result.failure_message == "Provider denied permission for message delivery"
    assert (
        result.failure_classification
        == OutboundDeliveryFailureClassification.PERMANENT
    )
    assert "must-not-be-persisted" not in str(result)
    assert "sensitive provider response" not in str(result)


def test_whatsapp_cloud_adapter_maps_http_failure_as_temporary_network_error():
    account = make_account()
    action = make_action(account)
    adapter, transport = make_adapter()

    def raise_transport_error(url, *, payload, headers, timeout):
        del url, payload, headers, timeout
        raise httpx.ConnectError("synthetic transport failure with no credentials")

    transport.post = raise_transport_error

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "whatsapp_cloud_network_error"
    assert result.failure_message == "WhatsApp Cloud delivery failed"
    assert (
        result.failure_classification
        == OutboundDeliveryFailureClassification.TEMPORARY
    )
    assert "synthetic transport failure" not in str(result)


def test_whatsapp_cloud_adapter_maps_rate_limit_failure():
    account = make_account()
    action = make_action(account)

    adapter, _ = make_adapter(
        response=WhatsAppCloudHttpResponse(
            status_code=429,
            headers={},
            body=None,
        )
    )

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "whatsapp_cloud_rate_limited"
    assert (
        result.failure_classification
        == OutboundDeliveryFailureClassification.RATE_LIMIT
    )


def test_whatsapp_cloud_adapter_maps_server_failure_as_temporary():
    account = make_account()
    action = make_action(account)

    adapter, _ = make_adapter(
        response=WhatsAppCloudHttpResponse(
            status_code=500,
            headers={},
            body=None,
        )
    )

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "whatsapp_cloud_server_error"
    assert (
        result.failure_classification
        == OutboundDeliveryFailureClassification.TEMPORARY
    )


def test_default_registry_can_register_native_whatsapp_cloud_adapter():
    adapter, _ = make_adapter()

    registry = default_delivery_adapter_registry(
        whatsapp_cloud_adapter=adapter,
    )

    assert registry.get(WHATSAPP_CLOUD_PROVIDER) is adapter

def test_outbound_delivery_from_settings_registers_native_whatsapp_adapter():
    settings = Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="demo",
        require_human_approval=True,
        auth_token_secret="test-auth-token-secret-32-byte-value",
    )

    engine = create_engine("sqlite://")

    with Session(engine) as session:
        service = OutboundIntegrationDeliveryService.from_settings(
            session,
            settings,
        )

        adapter = service.adapter_registry.get(WHATSAPP_CLOUD_PROVIDER)

        assert isinstance(adapter, WhatsAppCloudDeliveryAdapter)
