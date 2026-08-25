import hmac
import json
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.models import (
    IntegrationAccount,
    OutboundDeliveryFailureClassification,
    OutboundIntegrationAction,
    OutboundIntegrationActionType,
)
from app.services.delivery_adapters import (
    TikTokBusinessDeliveryAdapter,
    TikTokBusinessHttpResponse,
    normalize_tiktok_business_response,
)
from app.services.tiktok_business import (
    TikTokInboundNormalizer,
    TikTokInboundUnsupportedEventError,
    TikTokWebhookVerificationError,
    verify_tiktok_webhook_signature,
)

APP_SECRET = "unit-tiktok-app-secret"


class _References:
    def get_for_integration_account(self, account, purpose):
        assert account.provider == "tiktok_dm"
        assert purpose == "api_access_token"
        return SimpleNamespace(secret_reference="TIKTOK_ACCESS_TOKEN_REFERENCE")


class _Secrets:
    def __init__(self, value="unit-access-token"):
        self.value = value

    def resolve(self, reference):
        assert reference == "TIKTOK_ACCESS_TOKEN_REFERENCE"
        return self.value


class _Transport:
    def __init__(self, response=None, error=None):
        self.response = response or TikTokBusinessHttpResponse(
            status_code=200,
            headers={},
            body={"code": 0, "data": {"message": {"message_id": "tt-mid-1"}}},
        )
        self.error = error
        self.calls = []

    def post(self, url, *, payload, headers, timeout):
        self.calls.append(
            {"url": url, "payload": payload, "headers": headers, "timeout": timeout}
        )
        if self.error is not None:
            raise self.error
        return self.response


def _account(*, eligible=False):
    return IntegrationAccount(
        workspace_id=uuid4(),
        provider="tiktok_dm",
        external_account_id="business-open-id",
        comment_to_message_eligible=eligible,
        secret_reference="REFERENCE_ONLY",
        credential_hash="a" * 64,
    )


def _action(*, channel="tiktok_dm", target="conversation-1"):
    account = _account()
    return OutboundIntegrationAction(
        workspace_id=account.workspace_id,
        integration_account_id=account.id,
        external_target_id=target,
        action_type=OutboundIntegrationActionType.SEND_MESSAGE,
        content="Thanks — here are the details.",
        payload={"channel": channel},
        idempotency_key=uuid4().hex,
    )


def _adapter(transport, *, token="unit-access-token"):
    return TikTokBusinessDeliveryAdapter(
        _References(),
        api_base_url="https://business-api.tiktok.com/open_api",
        api_version="v1.3",
        transport=transport,
        secret_resolver=_Secrets(token),
    )


def _payload(event, content):
    return {
        "client_key": "client-key",
        "event": event,
        "user_openid": "business-open-id",
        "content": json.dumps(content, separators=(",", ":")),
    }


def _identity_content(**values):
    return {
        "from_user": {"role": "personal_account"},
        "to_user": {"role": "business_account"},
        "unique_identifier": "person-open-id",
        "from": "Prospect",
        **values,
    }


def test_tiktok_signature_uses_timestamp_dot_raw_body_and_rejects_invalid_or_stale():
    body = b'{"event":"im_receive_msg"}'
    timestamp = 1_800_000_000
    signature = hmac.new(
        APP_SECRET.encode(), str(timestamp).encode() + b"." + body, sha256
    ).hexdigest()

    verify_tiktok_webhook_signature(
        payload=body,
        signature_header=f"t={timestamp},s={signature}",
        app_secret=APP_SECRET,
        max_age_seconds=300,
        now=lambda: timestamp + 10,
    )

    for header, now in (
        (f"t={timestamp},s={'0' * 64}", timestamp + 10),
        (f"t={timestamp},s={signature}", timestamp + 301),
        (f"t={timestamp},t={timestamp},s={signature}", timestamp),
    ):
        with pytest.raises(TikTokWebhookVerificationError):
            verify_tiktok_webhook_signature(
                payload=body,
                signature_header=header,
                app_secret=APP_SECRET,
                max_age_seconds=300,
                now=lambda value=now: value,
            )


def test_tiktok_direct_and_high_intent_comment_normalization_use_documented_fields():
    normalizer = TikTokInboundNormalizer()
    direct = normalizer.normalize(
        _payload(
            "im_receive_msg",
            _identity_content(
                type="text",
                text={"body": "Is this available?"},
                message_id="message-1",
                conversation_id="conversation-1",
                timestamp="1800000000",
            ),
        ),
        expected_app_id="client-key",
        expected_account_id="business-open-id",
    )
    comment = normalizer.normalize(
        _payload(
            "im_receive_high_intent_comment",
            _identity_content(
                comment_id="comment-1",
                comment_text="Interested",
                timestamp=1_800_000_001,
            ),
        ),
        expected_app_id="client-key",
        expected_account_id="business-open-id",
    )

    assert (direct.kind, direct.channel, direct.provider_event_id) == (
        "direct_message",
        "tiktok_dm",
        "message-1",
    )
    assert direct.external_conversation_id == "conversation-1"
    assert direct.sender_external_id == "person-open-id"
    assert (comment.kind, comment.channel, comment.provider_event_id) == (
        "comment",
        "tiktok_comment",
        "comment-1",
    )
    assert comment.post_or_media_id is None
    assert comment.parent_comment_id is None

    with pytest.raises(TikTokInboundUnsupportedEventError):
        normalizer.normalize(
            _payload("im_read_msg", {}),
            expected_app_id="client-key",
            expected_account_id="business-open-id",
        )


def test_tiktok_direct_delivery_uses_fixed_business_api_contract_and_delivery_id():
    transport = _Transport()
    account = _account()
    action = _action()

    result = _adapter(transport).deliver(action, account)

    assert result.delivered is True
    assert result.provider_delivery_id == "tt-mid-1"
    assert transport.calls == [
        {
            "url": "https://business-api.tiktok.com/open_api/v1.3/business/message/send/",
            "payload": {
                "business_id": "business-open-id",
                "message_type": "TEXT",
                "text": {"body": "Thanks — here are the details."},
                "recipient_type": "CONVERSATION",
                "recipient": "conversation-1",
            },
            "headers": {
                "Access-Token": "unit-access-token",
                "Content-Type": "application/json",
            },
            "timeout": transport.calls[0]["timeout"],
        }
    ]


def test_tiktok_comment_to_message_is_default_denied_and_uses_comment_reply_contract():
    transport = _Transport()
    action = _action(channel="tiktok_comment", target="comment-1")

    denied = _adapter(transport).deliver(action, _account())
    delivered = _adapter(transport).deliver(action, _account(eligible=True))

    assert denied.failure_code == "provider_capability_unavailable"
    assert denied.failure_classification == OutboundDeliveryFailureClassification.PERMANENT
    assert len(transport.calls) == 1
    assert delivered.provider_delivery_id == "tt-mid-1"
    assert transport.calls[0]["payload"] == {
        "business_id": "business-open-id",
        "message_type": "TEXT",
        "text": {"body": "Thanks — here are the details."},
        "direct_reply": {
            "reply_type": "COMMENT_REPLY",
            "comment_reply": {"comment_id": "comment-1"},
        },
    }


@pytest.mark.parametrize(
    ("status", "body", "code", "classification"),
    [
        (401, None, "provider_reconnect_required", "authentication"),
        (200, {"code": 40105}, "provider_reconnect_required", "authentication"),
        (403, None, "provider_permission_denied", "permanent"),
        (200, {"code": 40001}, "provider_permission_denied", "permanent"),
        (429, None, "tiktok_rate_limited", "rate_limit"),
        (200, {"code": 40064}, "tiktok_message_restricted", "permanent"),
    ],
)
def test_tiktok_safe_failure_classification(status, body, code, classification):
    result = normalize_tiktok_business_response(
        TikTokBusinessHttpResponse(status_code=status, headers={}, body=body)
    )

    assert result.failure_code == code
    assert result.failure_classification.value == classification
    assert APP_SECRET not in (result.failure_message or "")


def test_tiktok_transport_failure_is_temporary_and_token_is_not_in_result():
    token = "do-not-persist-this-token"
    transport = _Transport(error=httpx.ConnectError("provider unavailable"))

    result = _adapter(transport, token=token).deliver(_action(), _account())

    assert result.failure_code == "tiktok_network_error"
    assert result.failure_classification == OutboundDeliveryFailureClassification.TEMPORARY
    assert token not in repr(result)


def test_tiktok_adapter_rejects_arbitrary_api_host_configuration_before_io():
    transport = _Transport()
    adapter = TikTokBusinessDeliveryAdapter(
        _References(),
        api_base_url="https://attacker.invalid",
        api_version="v1.3",
        transport=transport,
        secret_resolver=_Secrets(),
    )

    result = adapter.deliver(_action(), _account())

    assert result.failure_code == "tiktok_configuration_invalid"
    assert transport.calls == []
