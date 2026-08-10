import hmac
import json
from hashlib import sha256
from pathlib import Path

import pytest

from app.services.whatsapp_cloud import (
    WHATSAPP_CLOUD_GRAPH_API_BASE_URL,
    WhatsAppCloudAccountMismatchError,
    WhatsAppCloudIgnoredEvent,
    WhatsAppCloudNormalizationError,
    WhatsAppCloudOutboundPayloadSecretError,
    WhatsAppCloudSignatureVerificationError,
    WhatsAppCloudUnsupportedMessageError,
    WhatsAppCloudWebhookVerificationError,
    assert_no_outbound_payload_secrets,
    build_text_send_request,
    normalize_text_message,
    verify_meta_signature,
    verify_webhook_challenge,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "whatsapp_cloud"
PHONE_NUMBER_ID = "555666777888999"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_valid_whatsapp_text_normalizes_to_minimal_transport_contract():
    normalized = normalize_text_message(
        _fixture("valid_text.json"),
        expected_recipient_account_id=PHONE_NUMBER_ID,
    )

    assert normalized.provider == "whatsapp_cloud"
    assert normalized.channel == "whatsapp_cloud"
    assert normalized.provider_event_id == (
        "wamid.HBgLMTU1NTc2NTQzMjEVAgASGBQzQUMzRjA0N0Y2MzY2QzA0AA"
    )
    assert normalized.sender_external_id == "15557654321"
    assert normalized.recipient_account_id == PHONE_NUMBER_ID
    assert normalized.content == "What is the monthly price?"
    assert normalized.timestamp == 1_720_000_000
    assert normalized.provider_metadata == {
        "waba_id": "111122223333444",
        "display_phone_number": "15551234567",
        "message_type": "text",
    }


def test_duplicate_whatsapp_text_keeps_the_same_canonical_event_identity():
    first = normalize_text_message(_fixture("valid_text.json"))
    duplicate = normalize_text_message(_fixture("duplicate_text.json"))

    assert duplicate.provider_event_id == first.provider_event_id


def test_status_only_webhook_is_classified_without_text_normalization():
    with pytest.raises(WhatsAppCloudIgnoredEvent) as exc_info:
        normalize_text_message(_fixture("status_only.json"))

    assert exc_info.value.reason == "status_event"


def test_unsupported_media_does_not_become_fake_text():
    with pytest.raises(WhatsAppCloudUnsupportedMessageError) as exc_info:
        normalize_text_message(_fixture("unsupported_media.json"))

    assert exc_info.value.message_type == "image"


def test_malformed_payload_fails_deterministically():
    with pytest.raises(WhatsAppCloudNormalizationError, match="text body"):
        normalize_text_message(_fixture("malformed.json"))


def test_wrong_account_phone_reference_is_rejected():
    with pytest.raises(WhatsAppCloudAccountMismatchError):
        normalize_text_message(
            _fixture("wrong_account_phone.json"),
            expected_recipient_account_id=PHONE_NUMBER_ID,
        )


def test_forged_workspace_fields_in_provider_payload_have_no_authority():
    payload = _fixture("valid_text.json")
    payload["workspace_slug"] = "company-b"
    payload["workspace_id"] = "00000000-0000-0000-0000-000000000000"
    payload["entry"][0]["changes"][0]["value"]["workspace_slug"] = "company-b"

    normalized = normalize_text_message(payload)

    assert normalized.recipient_account_id == PHONE_NUMBER_ID
    assert "workspace" not in str(normalized.provider_metadata).lower()


def test_meta_verification_contract_returns_challenge_only_for_valid_token():
    assert (
        verify_webhook_challenge(
            mode="subscribe",
            verify_token="configured-token",
            challenge="1158201444",
            configured_verify_token="configured-token",
        )
        == "1158201444"
    )

    for token in ("wrong-token", "", None):
        with pytest.raises(WhatsAppCloudWebhookVerificationError):
            verify_webhook_challenge(
                mode="subscribe",
                verify_token=token,
                challenge="1158201444",
                configured_verify_token="configured-token",
            )

    with pytest.raises(WhatsAppCloudWebhookVerificationError):
        verify_webhook_challenge(
            mode="not-subscribe",
            verify_token="configured-token",
            challenge="1158201444",
            configured_verify_token="configured-token",
        )


def test_meta_signature_verification_uses_raw_body_and_app_secret():
    payload = json.dumps(_fixture("valid_text.json"), separators=(",", ":")).encode()
    app_secret = "fake-meta-app-secret"
    signature = "sha256=" + hmac.new(app_secret.encode(), payload, sha256).hexdigest()

    verify_meta_signature(
        payload=payload,
        signature_header=signature,
        app_secret=app_secret,
    )

    with pytest.raises(WhatsAppCloudSignatureVerificationError):
        verify_meta_signature(
            payload=payload + b" ",
            signature_header=signature,
            app_secret=app_secret,
        )
    with pytest.raises(WhatsAppCloudSignatureVerificationError):
        verify_meta_signature(
            payload=payload,
            signature_header=None,
            app_secret=app_secret,
        )


def test_outbound_text_mapper_uses_configured_graph_version_without_credentials():
    request = build_text_send_request(
        phone_number_id=PHONE_NUMBER_ID,
        recipient="15557654321",
        text="Here is the pricing information.",
        graph_api_base_url=WHATSAPP_CLOUD_GRAPH_API_BASE_URL,
        graph_api_version="vTEST",
    )

    assert request.method == "POST"
    assert request.url == (
        "https://graph.facebook.com/vTEST/555666777888999/messages"
    )
    assert request.body == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "15557654321",
        "type": "text",
        "text": {
            "preview_url": False,
            "body": "Here is the pricing information.",
        },
    }
    serialized = json.dumps(request.body).lower()
    assert "authorization" not in serialized
    assert "access_token" not in serialized


def test_outbound_payload_validator_blocks_provider_credentials_recursively():
    assert_no_outbound_payload_secrets({"format": "plain_text"})

    for payload in (
        {"access_token": "fake-token"},
        {"nested": {"app_secret": "fake-secret"}},
        [{"authorization": "fake-auth"}],
    ):
        with pytest.raises(WhatsAppCloudOutboundPayloadSecretError):
            assert_no_outbound_payload_secrets(payload)
