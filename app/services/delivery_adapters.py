"""Provider-neutral contracts for outbound delivery adapters."""

import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

import httpx

from app.integrations.providers import (
    FACEBOOK_MESSENGER_PROVIDER,
    GENERIC_HMAC_PROVIDER,
    GENERIC_WEBHOOK_DELIVERY_PROVIDERS,
    INSTAGRAM_DM_PROVIDER,
    INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE,
    INSTAGRAM_LOGIN_AUTH_MODE,
    META_MESSAGING_PROVIDERS,
    TIKTOK_COMMENT_CHANNEL,
    TIKTOK_DM_PROVIDER,
    WHATSAPP_CLOUD_PROVIDER,
)
from app.models import (
    IntegrationAccount,
    OutboundDeliveryFailureClassification,
    OutboundIntegrationAction,
    OutboundIntegrationActionType,
)
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceNotFoundError,
    IntegrationCredentialReferenceService,
)
from app.services.secret_resolver import EnvironmentSecretResolver, SecretResolver
from app.services.whatsapp_cloud import build_text_send_request

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
        body = self._serialize_action(action, account)
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
    def _serialize_action(
        action: OutboundIntegrationAction, account: IntegrationAccount
    ) -> bytes:
        import json

        return json.dumps(
            {
                "provider": account.provider,
                "integration_account_id": str(account.id),
                "external_account_id": account.external_account_id,
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

_WHATSAPP_CLOUD_API_ACCESS_TOKEN_PURPOSE = "api_access_token"


@dataclass(frozen=True)
class WhatsAppCloudHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: dict[str, Any] | None = None


class WhatsAppCloudHttpTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> WhatsAppCloudHttpResponse: ...


class HttpxWhatsAppCloudHttpTransport:
    """HTTP boundary for direct WhatsApp Cloud API delivery."""

    def post(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> WhatsAppCloudHttpResponse:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                url,
                json=payload,
                headers=headers,
            )

        try:
            body = response.json()
        except ValueError:
            body = None

        if not isinstance(body, dict):
            body = None

        return WhatsAppCloudHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body,
        )


class WhatsAppCloudDeliveryAdapter:
    """Direct outbound text delivery through the WhatsApp Cloud API."""

    capabilities = DEFAULT_DELIVERY_ADAPTER_CAPABILITIES

    def __init__(
        self,
        credential_reference_service: IntegrationCredentialReferenceService,
        *,
        graph_api_base_url: str,
        graph_api_version: str,
        connect_timeout_seconds: float = 5,
        read_timeout_seconds: float = 15,
        transport: WhatsAppCloudHttpTransport | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.credential_reference_service = credential_reference_service
        self.graph_api_base_url = graph_api_base_url.strip()
        self.graph_api_version = graph_api_version.strip()
        self.timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self.transport = transport or HttpxWhatsAppCloudHttpTransport()
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()

    def deliver(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> DeliveryAdapterResult:
        if account.provider != WHATSAPP_CLOUD_PROVIDER:
            return DeliveryAdapterResult.failure(
                "whatsapp_cloud_provider_mismatch",
                "Integration account is not a WhatsApp Cloud account",
                OutboundDeliveryFailureClassification.VALIDATION,
            )

        if not account.external_account_id or not account.external_account_id.strip():
            return DeliveryAdapterResult.failure(
                "whatsapp_cloud_phone_number_id_missing",
                "WhatsApp Cloud phone number ID is not configured",
                OutboundDeliveryFailureClassification.VALIDATION,
            )

        if not action.external_target_id or not action.external_target_id.strip():
            return DeliveryAdapterResult.failure(
                "whatsapp_cloud_recipient_missing",
                "WhatsApp Cloud recipient is required",
                OutboundDeliveryFailureClassification.VALIDATION,
            )

        if not action.content or not action.content.strip():
            return DeliveryAdapterResult.failure(
                "whatsapp_cloud_content_missing",
                "WhatsApp Cloud message content is required",
                OutboundDeliveryFailureClassification.VALIDATION,
            )

        if not self.graph_api_base_url or not self.graph_api_version:
            return DeliveryAdapterResult.failure(
                "whatsapp_cloud_configuration_missing",
                "WhatsApp Cloud Graph API configuration is missing",
                OutboundDeliveryFailureClassification.VALIDATION,
            )

        try:
            credential_reference = (
                self.credential_reference_service.get_for_integration_account(
                    account,
                    _WHATSAPP_CLOUD_API_ACCESS_TOKEN_PURPOSE,
                )
            )
        except IntegrationCredentialReferenceNotFoundError:
            return DeliveryAdapterResult.failure(
                "whatsapp_cloud_access_token_reference_missing",
                "WhatsApp Cloud API access token reference is not configured",
                OutboundDeliveryFailureClassification.AUTHENTICATION,
            )

        access_token = self.secret_resolver.resolve(
            credential_reference.secret_reference
        )
        if not access_token:
            return DeliveryAdapterResult.failure(
                "whatsapp_cloud_access_token_unavailable",
                "WhatsApp Cloud API access token is unavailable",
                OutboundDeliveryFailureClassification.AUTHENTICATION,
            )

        request = build_text_send_request(
            phone_number_id=account.external_account_id,
            recipient=action.external_target_id,
            text=action.content,
            graph_api_base_url=self.graph_api_base_url,
            graph_api_version=self.graph_api_version,
        )

        try:
            response = self.transport.post(
                request.url,
                payload=request.body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            return DeliveryAdapterResult.failure(
                "whatsapp_cloud_network_error",
                "WhatsApp Cloud delivery failed",
                OutboundDeliveryFailureClassification.TEMPORARY,
            )

        return normalize_whatsapp_cloud_response(response)


def normalize_whatsapp_cloud_response(
    response: WhatsAppCloudHttpResponse,
) -> DeliveryAdapterResult:
    """Map WhatsApp Cloud HTTP outcomes to provider-neutral delivery results."""
    status_code = response.status_code

    if 200 <= status_code < 300:
        provider_delivery_id = None

        if response.body is not None:
            messages = response.body.get("messages")
            if isinstance(messages, list) and messages:
                first_message = messages[0]
                if isinstance(first_message, dict):
                    message_id = first_message.get("id")
                    if isinstance(message_id, str) and message_id.strip():
                        provider_delivery_id = message_id

        return DeliveryAdapterResult.success(provider_delivery_id)

    if status_code == 429:
        return DeliveryAdapterResult.failure(
            "whatsapp_cloud_rate_limited",
            "WhatsApp Cloud delivery was rate limited",
            OutboundDeliveryFailureClassification.RATE_LIMIT,
        )

    if status_code == 401:
        return DeliveryAdapterResult.failure(
            "whatsapp_cloud_authentication_failed",
            "WhatsApp Cloud authentication was rejected",
            OutboundDeliveryFailureClassification.AUTHENTICATION,
        )

    if status_code == 403:
        return DeliveryAdapterResult.failure(
            "provider_permission_denied",
            "Provider denied permission for message delivery",
            OutboundDeliveryFailureClassification.PERMANENT,
        )

    if 400 <= status_code < 500:
        return DeliveryAdapterResult.failure(
            "whatsapp_cloud_validation_failed",
            "WhatsApp Cloud request was rejected",
            OutboundDeliveryFailureClassification.VALIDATION,
        )

    if 500 <= status_code < 600:
        return DeliveryAdapterResult.failure(
            "whatsapp_cloud_server_error",
            "WhatsApp Cloud service failed",
            OutboundDeliveryFailureClassification.TEMPORARY,
        )

    return DeliveryAdapterResult.failure(
        "whatsapp_cloud_response_unknown",
        "WhatsApp Cloud delivery returned an unknown response",
        OutboundDeliveryFailureClassification.UNKNOWN,
    )


@dataclass(frozen=True)
class MetaGraphHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: dict[str, Any] | None = None


class MetaGraphHttpTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> MetaGraphHttpResponse: ...


class HttpxMetaGraphHttpTransport:
    """HTTP boundary shared by native Messenger and Instagram delivery."""

    def post(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> MetaGraphHttpResponse:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = None
        return MetaGraphHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body if isinstance(body, dict) else None,
        )


class MetaGraphDeliveryAdapter:
    """Native Messenger and dual-mode Instagram messaging delivery."""

    capabilities = DEFAULT_DELIVERY_ADAPTER_CAPABILITIES

    def __init__(
        self,
        credential_reference_service: IntegrationCredentialReferenceService,
        *,
        graph_api_base_url: str,
        instagram_graph_api_base_url: str = "https://graph.instagram.com",
        graph_api_version: str,
        connect_timeout_seconds: float = 5,
        read_timeout_seconds: float = 15,
        transport: MetaGraphHttpTransport | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.credential_reference_service = credential_reference_service
        self.graph_api_base_url = graph_api_base_url.strip().rstrip("/")
        self.instagram_graph_api_base_url = (
            instagram_graph_api_base_url.strip().rstrip("/")
        )
        self.graph_api_version = graph_api_version.strip().lstrip("/")
        self.timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self.transport = transport or HttpxMetaGraphHttpTransport()
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()

    def deliver(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> DeliveryAdapterResult:
        validation_failure = self._validate(action, account)
        if validation_failure is not None:
            return validation_failure
        try:
            credential_reference = (
                self.credential_reference_service.get_for_integration_account(
                    account, "api_access_token"
                )
            )
        except IntegrationCredentialReferenceNotFoundError:
            return DeliveryAdapterResult.failure(
                "meta_access_token_reference_missing",
                "Meta API access token reference is not configured",
                OutboundDeliveryFailureClassification.AUTHENTICATION,
            )
        access_token = self.secret_resolver.resolve(
            credential_reference.secret_reference
        )
        if not access_token:
            return DeliveryAdapterResult.failure(
                "meta_access_token_unavailable",
                "Meta API access token is unavailable",
                OutboundDeliveryFailureClassification.AUTHENTICATION,
            )
        url, payload = self._request(action, account)
        try:
            response = self.transport.post(
                url,
                payload=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            return DeliveryAdapterResult.failure(
                "meta_network_error",
                "Meta message delivery failed",
                OutboundDeliveryFailureClassification.TEMPORARY,
            )
        return normalize_meta_graph_response(response)

    def _validate(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> DeliveryAdapterResult | None:
        if account.provider not in META_MESSAGING_PROVIDERS:
            return DeliveryAdapterResult.failure(
                "meta_provider_mismatch",
                "Integration account is not a supported Meta messaging account",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if (
            account.provider == INSTAGRAM_DM_PROVIDER
            and account.provider_auth_mode
            not in {None, INSTAGRAM_FACEBOOK_LOGIN_AUTH_MODE, INSTAGRAM_LOGIN_AUTH_MODE}
        ):
            return DeliveryAdapterResult.failure(
                "meta_provider_auth_mode_invalid",
                "Instagram provider authentication mode is unsupported",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        channel = str(action.payload.get("channel") or account.provider)
        supported_channels = {
            FACEBOOK_MESSENGER_PROVIDER: {
                FACEBOOK_MESSENGER_PROVIDER,
                "facebook_comment",
            },
            INSTAGRAM_DM_PROVIDER: {
                INSTAGRAM_DM_PROVIDER,
                "instagram_comment",
            },
        }
        if channel not in supported_channels[account.provider]:
            return DeliveryAdapterResult.failure(
                "meta_channel_provider_mismatch",
                "Meta message channel does not match the integration provider",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if not account.external_account_id or not account.external_account_id.strip():
            return DeliveryAdapterResult.failure(
                "meta_account_id_missing",
                "Meta account identifier is not configured",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if not action.external_target_id or not action.external_target_id.strip():
            return DeliveryAdapterResult.failure(
                "meta_recipient_missing",
                "Meta message recipient is required",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if not action.content or not action.content.strip():
            return DeliveryAdapterResult.failure(
                "meta_content_missing",
                "Meta message content is required",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if (
            not self.graph_api_base_url
            or not self.instagram_graph_api_base_url
            or not self.graph_api_version
        ):
            return DeliveryAdapterResult.failure(
                "meta_configuration_missing",
                "Meta Graph API configuration is missing",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        return None

    def _request(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> tuple[str, dict[str, Any]]:
        graph_api_base_url = self._graph_api_base_url(account)
        channel = str(action.payload.get("channel") or account.provider)
        if channel == "facebook_comment":
            return (
                (
                    f"{self.graph_api_base_url}/{self.graph_api_version}/"
                    f"{action.external_target_id}/private_replies"
                ),
                {"message": action.content},
            )
        if channel == "instagram_comment":
            return (
                (
                    f"{graph_api_base_url}/{self.graph_api_version}/"
                    f"{account.external_account_id}/messages"
                ),
                {
                    "recipient": {"comment_id": action.external_target_id},
                    "message": {"text": action.content},
                },
            )
        body: dict[str, Any] = {
            "recipient": {"id": action.external_target_id},
            "message": {"text": action.content},
        }
        if account.provider == FACEBOOK_MESSENGER_PROVIDER:
            body["messaging_type"] = "RESPONSE"
        elif account.provider != INSTAGRAM_DM_PROVIDER:
            raise ValueError("Unsupported Meta messaging provider")
        return (
            (
                f"{graph_api_base_url}/{self.graph_api_version}/"
                f"{account.external_account_id}/messages"
            ),
            body,
        )

    def _graph_api_base_url(self, account: IntegrationAccount) -> str:
        if account.provider == FACEBOOK_MESSENGER_PROVIDER:
            return self.graph_api_base_url
        if (
            account.provider == INSTAGRAM_DM_PROVIDER
            and account.provider_auth_mode == INSTAGRAM_LOGIN_AUTH_MODE
        ):
            return self.instagram_graph_api_base_url
        return self.graph_api_base_url


def normalize_meta_graph_response(
    response: MetaGraphHttpResponse,
) -> DeliveryAdapterResult:
    if 200 <= response.status_code < 300:
        body = response.body or {}
        provider_delivery_id = body.get("message_id") or body.get("id")
        if isinstance(provider_delivery_id, str) and provider_delivery_id.strip():
            return DeliveryAdapterResult.success(provider_delivery_id.strip())
        return DeliveryAdapterResult.failure(
            "meta_delivery_id_missing",
            "Meta delivery response did not include a message identifier",
            OutboundDeliveryFailureClassification.UNKNOWN,
        )
    error = (response.body or {}).get("error")
    error = error if isinstance(error, dict) else {}
    provider_code = error.get("code")
    provider_subcode = error.get("error_subcode")
    if response.status_code == 401 or provider_code == 190:
        return DeliveryAdapterResult.failure(
            "meta_authentication_failed",
            "Meta rejected the configured credentials",
            OutboundDeliveryFailureClassification.AUTHENTICATION,
        )
    if provider_code == 10903 or provider_subcode == 2534014:
        return DeliveryAdapterResult.failure(
            "meta_private_reply_unavailable",
            "Meta private reply is unavailable for this comment",
            OutboundDeliveryFailureClassification.PERMANENT,
        )
    if provider_code == 3:
        return DeliveryAdapterResult.failure(
            "meta_capability_unavailable",
            "Meta account capability is unavailable",
            OutboundDeliveryFailureClassification.PERMANENT,
        )
    if response.status_code == 403 or provider_code in {10, 200, 230}:
        return DeliveryAdapterResult.failure(
            "meta_permission_denied",
            "Meta denied the required provider permission",
            OutboundDeliveryFailureClassification.PERMANENT,
        )
    if response.status_code == 429:
        return DeliveryAdapterResult.failure(
            "meta_rate_limited",
            "Meta rate limited message delivery",
            OutboundDeliveryFailureClassification.RATE_LIMIT,
        )
    if response.status_code in {400, 404}:
        return DeliveryAdapterResult.failure(
            "meta_request_rejected",
            "Meta rejected the message request",
            OutboundDeliveryFailureClassification.VALIDATION,
        )
    if 500 <= response.status_code < 600:
        return DeliveryAdapterResult.failure(
            "meta_server_error",
            "Meta message delivery is temporarily unavailable",
            OutboundDeliveryFailureClassification.TEMPORARY,
        )
    return DeliveryAdapterResult.failure(
        "meta_response_unknown",
        "Meta message delivery returned an unknown response",
        OutboundDeliveryFailureClassification.UNKNOWN,
    )


_TIKTOK_BUSINESS_API_ACCESS_TOKEN_PURPOSE = "api_access_token"
_TIKTOK_BUSINESS_API_BASE_URL = "https://business-api.tiktok.com/open_api"


@dataclass(frozen=True)
class TikTokBusinessHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: dict[str, Any] | None = None


class TikTokBusinessHttpTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> TikTokBusinessHttpResponse: ...


class HttpxTikTokBusinessHttpTransport:
    """HTTP boundary for TikTok Business Messaging delivery."""

    def post(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> TikTokBusinessHttpResponse:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = None
        return TikTokBusinessHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body if isinstance(body, dict) else None,
        )


class TikTokBusinessDeliveryAdapter:
    """Native text delivery for TikTok Business Messaging and Comment-to-Message."""

    capabilities = DeliveryAdapterCapabilities(
        supported_action_types=frozenset({OutboundIntegrationActionType.SEND_MESSAGE}),
        max_content_length=6_000,
    )

    def __init__(
        self,
        credential_reference_service: IntegrationCredentialReferenceService,
        *,
        api_base_url: str,
        api_version: str,
        connect_timeout_seconds: float = 5,
        read_timeout_seconds: float = 15,
        transport: TikTokBusinessHttpTransport | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.credential_reference_service = credential_reference_service
        self.api_base_url = api_base_url.strip().rstrip("/")
        self.api_version = api_version.strip().strip("/")
        self.timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self.transport = transport or HttpxTikTokBusinessHttpTransport()
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()

    def deliver(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> DeliveryAdapterResult:
        validation_failure = self._validate(action, account)
        if validation_failure is not None:
            return validation_failure
        try:
            reference = self.credential_reference_service.get_for_integration_account(
                account,
                _TIKTOK_BUSINESS_API_ACCESS_TOKEN_PURPOSE,
            )
        except IntegrationCredentialReferenceNotFoundError:
            return DeliveryAdapterResult.failure(
                "tiktok_access_token_reference_missing",
                "TikTok API access token reference is not configured",
                OutboundDeliveryFailureClassification.AUTHENTICATION,
            )
        access_token = self.secret_resolver.resolve(reference.secret_reference)
        if not access_token:
            return DeliveryAdapterResult.failure(
                "provider_reconnect_required",
                "Provider credentials require refresh or reconnect",
                OutboundDeliveryFailureClassification.AUTHENTICATION,
            )
        try:
            response = self.transport.post(
                (
                    f"{self.api_base_url}/{self.api_version}/"
                    "business/message/send/"
                ),
                payload=self._request_body(action, account),
                headers={
                    "Access-Token": access_token,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            return DeliveryAdapterResult.failure(
                "tiktok_network_error",
                "TikTok message delivery failed",
                OutboundDeliveryFailureClassification.TEMPORARY,
            )
        return normalize_tiktok_business_response(response)

    def _validate(
        self,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> DeliveryAdapterResult | None:
        if account.provider != TIKTOK_DM_PROVIDER:
            return DeliveryAdapterResult.failure(
                "tiktok_provider_mismatch",
                "Integration account is not a TikTok messaging account",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if not account.external_account_id or not account.external_account_id.strip():
            return DeliveryAdapterResult.failure(
                "tiktok_business_id_missing",
                "TikTok Business Account identifier is not configured",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if not action.external_target_id or not action.external_target_id.strip():
            return DeliveryAdapterResult.failure(
                "tiktok_recipient_missing",
                "TikTok message recipient is required",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if not action.content or not action.content.strip():
            return DeliveryAdapterResult.failure(
                "tiktok_content_missing",
                "TikTok message content is required",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if (
            self.api_base_url != _TIKTOK_BUSINESS_API_BASE_URL
            or self.api_version != "v1.3"
        ):
            return DeliveryAdapterResult.failure(
                "tiktok_configuration_invalid",
                "TikTok Business API configuration is invalid",
                OutboundDeliveryFailureClassification.VALIDATION,
            )
        if (
            action.payload.get("channel") == TIKTOK_COMMENT_CHANNEL
            and not account.comment_to_message_eligible
        ):
            return DeliveryAdapterResult.failure(
                "provider_capability_unavailable",
                "TikTok Comment-to-Message eligibility is not confirmed",
                OutboundDeliveryFailureClassification.PERMANENT,
            )
        return None

    @staticmethod
    def _request_body(
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "business_id": account.external_account_id,
            "message_type": "TEXT",
            "text": {"body": action.content},
        }
        if action.payload.get("channel") == TIKTOK_COMMENT_CHANNEL:
            body["direct_reply"] = {
                "reply_type": "COMMENT_REPLY",
                "comment_reply": {"comment_id": action.external_target_id},
            }
        else:
            body["recipient_type"] = "CONVERSATION"
            body["recipient"] = action.external_target_id
        return body


def normalize_tiktok_business_response(
    response: TikTokBusinessHttpResponse,
) -> DeliveryAdapterResult:
    if response.status_code == 401:
        return _tiktok_reconnect_required()
    if response.status_code == 403:
        return DeliveryAdapterResult.failure(
            "provider_permission_denied",
            "Provider denied permission for message delivery",
            OutboundDeliveryFailureClassification.PERMANENT,
        )
    if response.status_code == 429:
        return DeliveryAdapterResult.failure(
            "tiktok_rate_limited",
            "TikTok rate limited message delivery",
            OutboundDeliveryFailureClassification.RATE_LIMIT,
        )
    if 500 <= response.status_code < 600:
        return DeliveryAdapterResult.failure(
            "tiktok_server_error",
            "TikTok message delivery is temporarily unavailable",
            OutboundDeliveryFailureClassification.TEMPORARY,
        )
    body = response.body or {}
    code = body.get("code")
    if 200 <= response.status_code < 300 and code == 0:
        data = body.get("data")
        message = data.get("message") if isinstance(data, dict) else None
        message_id = message.get("message_id") if isinstance(message, dict) else None
        if isinstance(message_id, str) and message_id.strip():
            return DeliveryAdapterResult.success(message_id.strip())
        return DeliveryAdapterResult.failure(
            "tiktok_delivery_id_missing",
            "TikTok delivery response did not include a message identifier",
            OutboundDeliveryFailureClassification.UNKNOWN,
        )
    if code == 40105:
        return _tiktok_reconnect_required()
    if code == 40001:
        return DeliveryAdapterResult.failure(
            "provider_permission_denied",
            "Provider denied permission for message delivery",
            OutboundDeliveryFailureClassification.PERMANENT,
        )
    if code == 40100:
        return DeliveryAdapterResult.failure(
            "tiktok_rate_limited",
            "TikTok rate limited message delivery",
            OutboundDeliveryFailureClassification.RATE_LIMIT,
        )
    if code == 40002:
        return DeliveryAdapterResult.failure(
            "tiktok_validation_failed",
            "TikTok rejected the message request",
            OutboundDeliveryFailureClassification.VALIDATION,
        )
    if code in {40007, 40064}:
        return DeliveryAdapterResult.failure(
            "tiktok_message_restricted",
            "TikTok messaging rules rejected the request",
            OutboundDeliveryFailureClassification.PERMANENT,
        )
    if code == 51065:
        return DeliveryAdapterResult.failure(
            "tiktok_server_error",
            "TikTok message delivery is temporarily unavailable",
            OutboundDeliveryFailureClassification.TEMPORARY,
        )
    if 400 <= response.status_code < 500:
        return DeliveryAdapterResult.failure(
            "tiktok_request_rejected",
            "TikTok rejected the message request",
            OutboundDeliveryFailureClassification.VALIDATION,
        )
    return DeliveryAdapterResult.failure(
        "tiktok_response_unknown",
        "TikTok message delivery returned an unknown response",
        OutboundDeliveryFailureClassification.UNKNOWN,
    )


def _tiktok_reconnect_required() -> DeliveryAdapterResult:
    return DeliveryAdapterResult.failure(
        "provider_reconnect_required",
        "Provider credentials require refresh or reconnect",
        OutboundDeliveryFailureClassification.AUTHENTICATION,
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

    def validate_action(self, provider: str, action: OutboundIntegrationAction) -> DeliveryAdapterResult | None:
        """Return a safe constraint failure before adapter I/O, if any."""
        capabilities = self.capabilities_for(provider)
        if capabilities is None:
            return DeliveryAdapterResult.failure(
                "adapter_capabilities_unavailable",
                "Delivery adapter capabilities are unavailable",
            )
        if action.action_type not in capabilities.supported_action_types:
            return DeliveryAdapterResult.failure(
                "unsupported_action_type",
                "Outbound action type is not supported by this delivery adapter",
            )
        if (
            capabilities.max_content_length is not None
            and len(action.content) > capabilities.max_content_length
        ):
            return DeliveryAdapterResult.failure(
                "content_too_long",
                "Outbound action content exceeds the delivery adapter limit",
            )
        return None


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
    whatsapp_cloud_adapter: DeliveryAdapter | None = None,
    meta_graph_adapter: DeliveryAdapter | None = None,
    tiktok_business_adapter: DeliveryAdapter | None = None,
) -> DeliveryAdapterRegistry:
    """Return the configured provider delivery adapters."""
    adapters: dict[str, DeliveryAdapter] = {
        GENERIC_HMAC_PROVIDER: NoopDeliveryAdapter()
    }

    if generic_webhook_adapter is not None:
        for provider in GENERIC_WEBHOOK_DELIVERY_PROVIDERS:
            adapters[provider] = generic_webhook_adapter

    if whatsapp_cloud_adapter is not None:
        adapters[WHATSAPP_CLOUD_PROVIDER] = whatsapp_cloud_adapter

    if meta_graph_adapter is not None:
        for provider in META_MESSAGING_PROVIDERS:
            adapters[provider] = meta_graph_adapter

    if tiktok_business_adapter is not None:
        adapters[TIKTOK_DM_PROVIDER] = tiktok_business_adapter

    return DeliveryAdapterRegistry(adapters)
