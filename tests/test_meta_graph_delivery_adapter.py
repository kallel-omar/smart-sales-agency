from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session, create_engine

from app.config import Settings
from app.models import (
    IntegrationAccount,
    OutboundDeliveryFailureClassification,
    OutboundIntegrationAction,
    OutboundIntegrationActionType,
)
from app.services.delivery_adapters import (
    MetaGraphDeliveryAdapter,
    MetaGraphHttpResponse,
    default_delivery_adapter_registry,
)
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceNotFoundError,
)
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


class FakeCredentialReferenceService:
    def get_for_integration_account(self, account, purpose):
        assert account.provider in {"facebook_messenger", "instagram_dm"}
        assert purpose == "api_access_token"
        return SimpleNamespace(secret_reference="INTEGRATION_SECRET_META_API_TOKEN")


class MissingCredentialReferenceService:
    def get_for_integration_account(self, account, purpose):
        del account, purpose
        raise IntegrationCredentialReferenceNotFoundError(
            "Integration credential reference not found"
        )


class FakeSecretResolver:
    def __init__(self, value="synthetic-meta-access-token"):
        self.value = value
        self.references = []

    def resolve(self, reference):
        self.references.append(reference)
        return self.value


class RecordingMetaTransport:
    def __init__(self, response=None):
        self.response = response or MetaGraphHttpResponse(
            status_code=200,
            headers={},
            body={"message_id": "mid.meta-success"},
        )
        self.calls = []

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


def make_account(provider):
    return IntegrationAccount(
        workspace_id=uuid4(),
        provider=provider,
        external_account_id=f"{provider}-account",
        credential_hash="a" * 64,
    )


def make_action(account, *, channel=None, target="meta-user-1"):
    return OutboundIntegrationAction(
        workspace_id=account.workspace_id,
        integration_account_id=account.id,
        external_target_id=target,
        action_type=OutboundIntegrationActionType.SEND_MESSAGE,
        content="Hello from HIRI",
        payload={"channel": channel or account.provider},
        idempotency_key=f"meta-adapter-{uuid4()}",
    )


def make_adapter(*, response=None, credential_service=None, resolver=None):
    transport = RecordingMetaTransport(response)
    adapter = MetaGraphDeliveryAdapter(
        credential_service or FakeCredentialReferenceService(),
        graph_api_base_url="https://graph.facebook.com",
        graph_api_version="v23.0",
        transport=transport,
        secret_resolver=resolver or FakeSecretResolver(),
    )
    return adapter, transport


@pytest.mark.parametrize(
    ("provider", "expected_extra"),
    [
        ("facebook_messenger", {"messaging_type": "RESPONSE"}),
        ("instagram_dm", {}),
    ],
)
def test_meta_adapter_maps_direct_messages(provider, expected_extra):
    account = make_account(provider)
    action = make_action(account)
    adapter, transport = make_adapter()

    result = adapter.deliver(action, account)

    assert result.delivered is True
    assert result.provider_delivery_id == "mid.meta-success"
    assert transport.calls[0]["url"] == (
        f"https://graph.facebook.com/v23.0/{provider}-account/messages"
    )
    assert transport.calls[0]["payload"] == {
        "recipient": {"id": "meta-user-1"},
        "message": {"text": "Hello from HIRI"},
        **expected_extra,
    }
    assert transport.calls[0]["headers"] == {
        "Authorization": "Bearer synthetic-meta-access-token",
        "Content-Type": "application/json",
    }


@pytest.mark.parametrize(
    ("provider", "channel"),
    [
        ("facebook_messenger", "facebook_comment"),
        ("instagram_dm", "instagram_comment"),
    ],
)
def test_meta_adapter_maps_comment_private_reply_into_same_provider(provider, channel):
    account = make_account(provider)
    action = make_action(account, channel=channel, target="comment-123")
    adapter, transport = make_adapter(
        response=MetaGraphHttpResponse(
            status_code=200,
            headers={},
            body={"message_id": "private-reply-1"},
        )
    )

    result = adapter.deliver(action, account)

    assert result.provider_delivery_id == "private-reply-1"
    assert transport.calls[0]["url"].endswith(
        f"/v23.0/{provider}-account/messages"
    )
    assert transport.calls[0]["payload"] == {
        "recipient": {"comment_id": "comment-123"},
        "message": {"text": "Hello from HIRI"},
    }


def test_meta_adapter_requires_access_token_reference_without_http_call():
    account = make_account("facebook_messenger")
    action = make_action(account)
    adapter, transport = make_adapter(
        credential_service=MissingCredentialReferenceService()
    )

    result = adapter.deliver(action, account)

    assert result.delivered is False
    assert result.failure_code == "meta_access_token_reference_missing"
    assert result.failure_classification == (
        OutboundDeliveryFailureClassification.AUTHENTICATION
    )
    assert transport.calls == []


@pytest.mark.parametrize(
    ("status", "failure_code", "classification"),
    [
        (401, "meta_authentication_failed", "authentication"),
        (429, "meta_rate_limited", "rate_limit"),
        (400, "meta_request_rejected", "validation"),
        (500, "meta_server_error", "temporary"),
    ],
)
def test_meta_adapter_maps_safe_provider_failures(
    status, failure_code, classification
):
    account = make_account("instagram_dm")
    action = make_action(account)
    adapter, _ = make_adapter(
        response=MetaGraphHttpResponse(status_code=status, headers={}, body={})
    )

    result = adapter.deliver(action, account)

    assert result.failure_code == failure_code
    assert result.failure_classification.value == classification


def test_default_registry_and_settings_select_native_meta_adapter():
    adapter, _ = make_adapter()
    registry = default_delivery_adapter_registry(meta_graph_adapter=adapter)
    assert registry.get("facebook_messenger") is adapter
    assert registry.get("instagram_dm") is adapter

    settings = Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="demo",
        require_human_approval=True,
        auth_token_secret="test-auth-token-secret-32-byte-value",
    )
    with Session(create_engine("sqlite://")) as session:
        configured = OutboundIntegrationDeliveryService.from_settings(
            session, settings
        )
        assert isinstance(
            configured.adapter_registry.get("facebook_messenger"),
            MetaGraphDeliveryAdapter,
        )
        assert isinstance(
            configured.adapter_registry.get("instagram_dm"),
            MetaGraphDeliveryAdapter,
        )
