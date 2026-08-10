"""WhatsApp Cloud transport contracts kept outside the Sales domain."""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from app.integrations.providers import WHATSAPP_CLOUD_PROVIDER

WHATSAPP_CLOUD_CHANNEL = WHATSAPP_CLOUD_PROVIDER
WHATSAPP_CLOUD_GRAPH_API_BASE_URL = "https://graph.facebook.com"

_FORBIDDEN_OUTBOUND_SECRET_KEYS = {
    "access_token",
    "app_secret",
    "authorization",
    "bearer_token",
    "client_secret",
    "permanent_token",
    "token",
    "verify_token",
    "whatsapp_access_token",
}


class WhatsAppCloudWebhookVerificationError(PermissionError):
    """Raised when Meta webhook challenge verification fails."""


class WhatsAppCloudSignatureVerificationError(PermissionError):
    """Raised when a Meta webhook payload signature is missing or invalid."""


class WhatsAppCloudNormalizationError(ValueError):
    """Raised when a WhatsApp Cloud webhook payload is not safely usable."""


class WhatsAppCloudUnsupportedMessageError(WhatsAppCloudNormalizationError):
    """Raised when the payload contains a known unsupported message type."""

    def __init__(self, message_type: str) -> None:
        self.message_type = message_type
        super().__init__(f"Unsupported WhatsApp Cloud message type: {message_type}")


class WhatsAppCloudAccountMismatchError(WhatsAppCloudNormalizationError):
    """Raised when a payload belongs to a different configured provider account."""


class WhatsAppCloudIgnoredEvent(WhatsAppCloudNormalizationError):
    """Raised when a signed provider event is valid noise for this task."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Ignored WhatsApp Cloud event: {reason}")


class WhatsAppCloudOutboundPayloadSecretError(ValueError):
    """Raised when persisted outbound payload data contains provider credentials."""


@dataclass(frozen=True)
class WhatsAppCloudTextMessage:
    """Normalized text-only event forwarded to FastAPI after provider checks."""

    provider: str
    channel: str
    provider_event_id: str
    sender_external_id: str
    recipient_account_id: str
    content: str
    timestamp: int | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WhatsAppCloudTextSendRequest:
    """Prepared WhatsApp Cloud text request without transport credentials."""

    method: str
    url: str
    body: dict[str, Any]


def verify_webhook_challenge(
    *,
    mode: str | None,
    verify_token: str | None,
    challenge: str | None,
    configured_verify_token: str | None,
) -> str:
    """Return Meta's challenge only when the configured verify token matches."""

    if (
        mode != "subscribe"
        or not verify_token
        or not challenge
        or not configured_verify_token
        or not hmac.compare_digest(verify_token, configured_verify_token)
    ):
        raise WhatsAppCloudWebhookVerificationError(
            "WhatsApp Cloud webhook verification failed"
        )
    return challenge


def verify_meta_signature(
    *,
    payload: bytes,
    signature_header: str | None,
    app_secret: str | None,
) -> None:
    """Verify Meta's X-Hub-Signature-256 over the exact raw request body."""

    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        raise WhatsAppCloudSignatureVerificationError(
            "WhatsApp Cloud webhook signature is invalid"
        )
    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"),
        payload,
        sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature_header, expected):
        raise WhatsAppCloudSignatureVerificationError(
            "WhatsApp Cloud webhook signature is invalid"
        )


def normalize_text_message(
    payload: dict[str, Any],
    *,
    expected_recipient_account_id: str | None = None,
) -> WhatsAppCloudTextMessage:
    """Normalize the first supported inbound WhatsApp text message.

    Status-only and non-message webhook deliveries are represented as ignored
    events so callers can acknowledge them without creating domain turns.
    """

    if not isinstance(payload, dict):
        raise WhatsAppCloudNormalizationError("WhatsApp Cloud payload must be an object")
    if payload.get("object") != "whatsapp_business_account":
        raise WhatsAppCloudNormalizationError("Unexpected WhatsApp Cloud webhook object")

    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries:
        raise WhatsAppCloudNormalizationError("WhatsApp Cloud payload has no entries")

    saw_status = False
    saw_non_message_change = False
    for entry in entries:
        if not isinstance(entry, dict):
            raise WhatsAppCloudNormalizationError("WhatsApp Cloud entry is invalid")
        changes = entry.get("changes")
        if not isinstance(changes, list) or not changes:
            raise WhatsAppCloudNormalizationError("WhatsApp Cloud entry has no changes")
        for change in changes:
            if not isinstance(change, dict):
                raise WhatsAppCloudNormalizationError("WhatsApp Cloud change is invalid")
            if change.get("field") != "messages":
                saw_non_message_change = True
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                raise WhatsAppCloudNormalizationError("WhatsApp Cloud change has no value")
            if value.get("messaging_product") != "whatsapp":
                raise WhatsAppCloudNormalizationError("Unexpected messaging product")

            metadata = value.get("metadata")
            if not isinstance(metadata, dict):
                raise WhatsAppCloudNormalizationError("WhatsApp Cloud metadata is missing")
            recipient_account_id = _required_text(
                metadata.get("phone_number_id"),
                "WhatsApp Cloud phone_number_id is missing",
            )
            if (
                expected_recipient_account_id is not None
                and recipient_account_id != expected_recipient_account_id
            ):
                raise WhatsAppCloudAccountMismatchError(
                    "WhatsApp Cloud account reference does not match"
                )

            messages = value.get("messages")
            if isinstance(messages, list) and messages:
                message = _message_object(messages[0])
                return _normalize_message(
                    message,
                    entry=entry,
                    metadata=metadata,
                    recipient_account_id=recipient_account_id,
                )
            if value.get("statuses") is not None:
                saw_status = True

    if saw_status:
        raise WhatsAppCloudIgnoredEvent("status_event")
    if saw_non_message_change:
        raise WhatsAppCloudIgnoredEvent("non_message_event")
    raise WhatsAppCloudNormalizationError("WhatsApp Cloud payload has no messages")


def build_text_send_request(
    *,
    phone_number_id: str,
    recipient: str,
    text: str,
    graph_api_base_url: str,
    graph_api_version: str,
) -> WhatsAppCloudTextSendRequest:
    """Map provider-neutral text intent to the minimal Cloud API request body."""

    phone_number_id = _non_empty(phone_number_id, "phone_number_id is required")
    recipient = _non_empty(recipient, "recipient is required")
    text = _non_empty(text, "text is required")
    graph_api_version = _non_empty(
        graph_api_version,
        "graph_api_version is required",
    )
    base_url = _non_empty(graph_api_base_url, "graph_api_base_url is required").rstrip("/")
    version = graph_api_version.strip().lstrip("/")
    url = f"{base_url}/{version}/{phone_number_id}/messages"
    return WhatsAppCloudTextSendRequest(
        method="POST",
        url=url,
        body={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text,
            },
        },
    )


def assert_no_outbound_payload_secrets(payload: Any) -> None:
    """Reject provider credentials inside FastAPI-persisted outbound payloads."""

    forbidden = _find_forbidden_secret_key(payload)
    if forbidden is not None:
        raise WhatsAppCloudOutboundPayloadSecretError(
            f"WhatsApp Cloud outbound payload must not contain provider credential key: {forbidden}"
        )


def _normalize_message(
    message: dict[str, Any],
    *,
    entry: dict[str, Any],
    metadata: dict[str, Any],
    recipient_account_id: str,
) -> WhatsAppCloudTextMessage:
    message_type = _required_text(
        message.get("type"),
        "WhatsApp Cloud message type is missing",
    )
    if message_type != "text":
        raise WhatsAppCloudUnsupportedMessageError(message_type)

    text = message.get("text")
    if not isinstance(text, dict):
        raise WhatsAppCloudNormalizationError("WhatsApp Cloud text payload is missing")
    body = _required_text(text.get("body"), "WhatsApp Cloud text body is missing")

    timestamp = message.get("timestamp")
    parsed_timestamp = _parse_timestamp(timestamp)
    return WhatsAppCloudTextMessage(
        provider=WHATSAPP_CLOUD_PROVIDER,
        channel=WHATSAPP_CLOUD_CHANNEL,
        provider_event_id=_required_text(
            message.get("id"),
            "WhatsApp Cloud message id is missing",
        ),
        sender_external_id=_required_text(
            message.get("from"),
            "WhatsApp Cloud sender id is missing",
        ),
        recipient_account_id=recipient_account_id,
        content=body,
        timestamp=parsed_timestamp,
        provider_metadata={
            "waba_id": entry.get("id"),
            "display_phone_number": metadata.get("display_phone_number"),
            "message_type": message_type,
        },
    )


def _message_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WhatsAppCloudNormalizationError("WhatsApp Cloud message is invalid")
    return value


def _required_text(value: Any, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WhatsAppCloudNormalizationError(message)
    return value.strip()


def _non_empty(value: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WhatsAppCloudNormalizationError(message)
    return value.strip()


def _parse_timestamp(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise WhatsAppCloudNormalizationError("WhatsApp Cloud timestamp is invalid")


def _find_forbidden_secret_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = _normalize_key(str(key))
            if (
                normalized_key in _FORBIDDEN_OUTBOUND_SECRET_KEYS
                or normalized_key.endswith("_secret")
                or normalized_key.endswith("_token")
            ):
                return str(key)
            nested = _find_forbidden_secret_key(nested_value)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for nested_value in value:
            nested = _find_forbidden_secret_key(nested_value)
            if nested is not None:
                return nested
    return None


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")
