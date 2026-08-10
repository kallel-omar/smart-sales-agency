"""Provider-neutral authentication for inbound integration webhooks."""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from app.integrations.providers import GENERIC_HMAC_PROVIDER, WHATSAPP_CLOUD_PROVIDER
from app.config import Settings
from app.models import IntegrationAccount
from app.services.secret_resolver import EnvironmentSecretResolver, SecretResolver


class WebhookAuthenticationError(PermissionError):
    """Raised when an inbound webhook cannot be authenticated."""


@dataclass(frozen=True)
class VerifiedWebhookMetadata:
    """Replay-protection metadata retained for a future replay-store boundary."""

    provider: str
    timestamp: int
    event_id: str | None


class ProviderWebhookVerifier(Protocol):
    """Provider adapter contract for authenticating raw inbound webhook bytes."""

    def verify(
        self,
        *,
        payload: bytes,
        signature: str | None,
        timestamp: str | None,
        event_id: str | None,
        secret: str | None,
        max_age_seconds: int,
    ) -> VerifiedWebhookMetadata: ...


class GenericHmacWebhookVerifier:
    """Small generic HMAC adapter used without coupling to a channel provider."""

    provider = GENERIC_HMAC_PROVIDER

    def __init__(self, provider: str = GENERIC_HMAC_PROVIDER) -> None:
        self.provider = provider

    def verify(
        self,
        *,
        payload: bytes,
        signature: str | None,
        timestamp: str | None,
        event_id: str | None,
        secret: str | None,
        max_age_seconds: int,
    ) -> VerifiedWebhookMetadata:
        if not secret or not signature or not timestamp:
            raise WebhookAuthenticationError("Webhook authentication failed")

        try:
            timestamp_value = int(timestamp)
        except ValueError as exc:
            raise WebhookAuthenticationError("Webhook authentication failed") from exc

        if abs(time.time() - timestamp_value) > max_age_seconds:
            raise WebhookAuthenticationError("Webhook authentication failed")

        signed_payload = timestamp.encode("ascii") + b"." + payload
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            signed_payload,
            sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise WebhookAuthenticationError("Webhook authentication failed")

        return VerifiedWebhookMetadata(
            provider=self.provider,
            timestamp=timestamp_value,
            event_id=event_id,
        )


class ProviderWebhookAuthenticationService:
    """Selects a provider verifier while keeping the core integration flow generic."""

    def __init__(
        self,
        settings: Settings,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.settings = settings
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self.verifiers: dict[str, ProviderWebhookVerifier] = {
            GENERIC_HMAC_PROVIDER: GenericHmacWebhookVerifier(GENERIC_HMAC_PROVIDER),
            WHATSAPP_CLOUD_PROVIDER: GenericHmacWebhookVerifier(WHATSAPP_CLOUD_PROVIDER),
        }

    def authenticate(
        self,
        account: IntegrationAccount,
        *,
        payload: bytes,
        signature: str | None,
        timestamp: str | None,
        event_id: str | None,
    ) -> VerifiedWebhookMetadata:
        verifier = self.verifiers.get(account.provider)
        if not verifier:
            raise WebhookAuthenticationError("Webhook authentication failed")

        secret = self.secret_resolver.resolve(account.secret_reference)

        return verifier.verify(
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            event_id=event_id,
            secret=secret,
            max_age_seconds=self.settings.webhook_max_age_seconds,
        )
