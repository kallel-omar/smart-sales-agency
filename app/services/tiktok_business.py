"""TikTok Business Messaging webhook verification and inbound normalization."""

from __future__ import annotations

import hmac
import json
import time
from collections.abc import Callable
from hashlib import sha256
from typing import Any

from app.integrations.providers import TIKTOK_COMMENT_CHANNEL, TIKTOK_DM_PROVIDER
from app.services.social_inbound import SocialInboundEvent


class TikTokWebhookVerificationError(PermissionError):
    pass


class TikTokInboundNormalizationError(ValueError):
    pass


class TikTokInboundAccountMismatchError(TikTokInboundNormalizationError):
    pass


class TikTokInboundUnsupportedEventError(TikTokInboundNormalizationError):
    pass


def verify_tiktok_webhook_signature(
    *,
    payload: bytes,
    signature_header: str | None,
    app_secret: str | None,
    max_age_seconds: int,
    now: Callable[[], float] = time.time,
) -> None:
    """Verify TikTok-Signature: HMAC-SHA256(secret, timestamp + '.' + raw body)."""
    if not signature_header or not app_secret:
        raise TikTokWebhookVerificationError("TikTok webhook verification failed")
    values: dict[str, str] = {}
    for item in signature_header.split(","):
        key, separator, value = item.strip().partition("=")
        if not separator or key not in {"t", "s"} or not value or key in values:
            raise TikTokWebhookVerificationError("TikTok webhook verification failed")
        values[key] = value
    timestamp_text = values.get("t")
    received_signature = values.get("s")
    if (
        timestamp_text is None
        or received_signature is None
        or not timestamp_text.isdigit()
        or len(received_signature) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in received_signature)
    ):
        raise TikTokWebhookVerificationError("TikTok webhook verification failed")
    timestamp = int(timestamp_text)
    current = int(now())
    if timestamp > current + max_age_seconds or current - timestamp > max_age_seconds:
        raise TikTokWebhookVerificationError("TikTok webhook verification failed")
    signed_payload = timestamp_text.encode() + b"." + payload
    expected = hmac.new(app_secret.encode(), signed_payload, sha256).hexdigest()
    if not hmac.compare_digest(expected, received_signature.lower()):
        raise TikTokWebhookVerificationError("TikTok webhook verification failed")


class TikTokInboundNormalizer:
    """Normalize only documented TikTok Business Messaging event shapes."""

    SUPPORTED_EVENTS = frozenset({"im_receive_msg", "im_receive_high_intent_comment"})

    def routing_account_id(self, payload: dict[str, Any]) -> str:
        return self._text(
            payload.get("user_openid"),
            "TikTok Business Account identifier is missing",
        )

    def normalize(
        self,
        payload: dict[str, Any],
        *,
        expected_app_id: str,
        expected_account_id: str | None,
    ) -> SocialInboundEvent:
        if not expected_app_id or payload.get("client_key") != expected_app_id:
            raise TikTokInboundAccountMismatchError("TikTok application does not match")
        account_id = self.routing_account_id(payload)
        if expected_account_id is None or account_id != expected_account_id:
            raise TikTokInboundAccountMismatchError("TikTok account does not match")
        event_name = self._text(payload.get("event"), "TikTok event name is missing")
        if event_name not in self.SUPPORTED_EVENTS:
            raise TikTokInboundUnsupportedEventError("TikTok event is not actionable")
        content = self._content(payload.get("content"))
        if event_name == "im_receive_msg":
            return self._direct_message(account_id, content)
        return self._high_intent_comment(account_id, content)

    def _direct_message(
        self,
        account_id: str,
        content: dict[str, Any],
    ) -> SocialInboundEvent:
        self._validate_roles(content)
        if content.get("type") != "text" or not isinstance(content.get("text"), dict):
            raise TikTokInboundUnsupportedEventError(
                "TikTok inbound message type is not supported"
            )
        return SocialInboundEvent(
            kind="direct_message",
            channel=TIKTOK_DM_PROVIDER,
            provider_event_id=self._text(
                content.get("message_id"), "TikTok message identifier is missing"
            ),
            sender_external_id=self._text(
                content.get("unique_identifier"), "TikTok sender identifier is missing"
            ),
            recipient_account_id=account_id,
            content=self._text(
                content["text"].get("body"), "TikTok message text is missing"
            ),
            display_name=self._optional_text(content.get("from")),
            timestamp=self._timestamp(content.get("timestamp")),
            message_type="text",
            external_conversation_id=self._text(
                content.get("conversation_id"), "TikTok conversation identifier is missing"
            ),
        )

    def _high_intent_comment(
        self,
        account_id: str,
        content: dict[str, Any],
    ) -> SocialInboundEvent:
        self._validate_roles(content)
        return SocialInboundEvent(
            kind="comment",
            channel=TIKTOK_COMMENT_CHANNEL,
            provider_event_id=self._text(
                content.get("comment_id"), "TikTok comment identifier is missing"
            ),
            sender_external_id=self._text(
                content.get("unique_identifier"), "TikTok commenter identifier is missing"
            ),
            recipient_account_id=account_id,
            content=self._text(
                content.get("comment_text"), "TikTok comment text is missing"
            ),
            display_name=self._optional_text(content.get("from")),
            timestamp=self._timestamp(content.get("timestamp")),
            message_type="text",
        )

    @staticmethod
    def _validate_roles(content: dict[str, Any]) -> None:
        sender = content.get("from_user")
        recipient = content.get("to_user")
        if (
            not isinstance(sender, dict)
            or sender.get("role") != "personal_account"
            or not isinstance(recipient, dict)
            or recipient.get("role") != "business_account"
        ):
            raise TikTokInboundNormalizationError("TikTok message identity is invalid")

    @staticmethod
    def _content(value: Any) -> dict[str, Any]:
        if not isinstance(value, str):
            raise TikTokInboundNormalizationError("TikTok event content is invalid")
        try:
            content = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise TikTokInboundNormalizationError("TikTok event content is invalid") from exc
        if not isinstance(content, dict):
            raise TikTokInboundNormalizationError("TikTok event content is invalid")
        return content

    @staticmethod
    def _text(value: Any, message: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise TikTokInboundNormalizationError(message)
        return value.strip()

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _timestamp(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        raise TikTokInboundNormalizationError("TikTok timestamp is invalid")
