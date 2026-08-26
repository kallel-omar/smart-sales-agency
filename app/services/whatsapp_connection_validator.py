"""Read-only WhatsApp Cloud provider connection validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx
from sqlmodel import Session

from app.config import Settings
from app.integrations.providers import (
    API_ACCESS_TOKEN_PURPOSE,
    WEBHOOK_APP_SECRET_PURPOSE,
    WEBHOOK_VERIFY_TOKEN_PURPOSE,
    WHATSAPP_CLOUD_PROVIDER,
)
from app.models import IntegrationAccount, IntegrationAccountConnectionStatus
from app.services.channel_connections import (
    ChannelConnectionValidationResult,
    ChannelConnectionValidatorRegistry,
)
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceNotFoundError,
    IntegrationCredentialReferenceService,
)
from app.services.secret_resolver import EnvironmentSecretResolver, SecretResolver

_GRAPH_API_HOST = "graph.facebook.com"
_ALLOWED_GRAPH_API_VERSIONS = frozenset({"v23.0"})
_GRAPH_API_VERSION_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+$")
_UNAVAILABLE_CHECKS = (
    "whatsapp_business_account_identity",
    "provider_webhook_subscription",
    "whatsapp_business_messaging_permission",
)


@dataclass(frozen=True)
class WhatsAppCloudValidationHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: dict[str, Any] | None = None


class WhatsAppCloudValidationHttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> WhatsAppCloudValidationHttpResponse: ...


class HttpxWhatsAppCloudValidationHttpTransport:
    """HTTP boundary for Meta's read-only business phone-number lookup."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> WhatsAppCloudValidationHttpResponse:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)

        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = None
        return WhatsAppCloudValidationHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body,
        )


class WhatsAppCloudConnectionValidator:
    """Validate one configured Phone Number ID without sending or mutating Meta."""

    def __init__(
        self,
        credential_reference_service: IntegrationCredentialReferenceService,
        *,
        graph_api_base_url: str,
        graph_api_version: str,
        connect_timeout_seconds: float = 5,
        read_timeout_seconds: float = 15,
        transport: WhatsAppCloudValidationHttpTransport | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.credential_reference_service = credential_reference_service
        self.graph_api_base_url = graph_api_base_url.strip().rstrip("/")
        self.graph_api_version = graph_api_version.strip()
        self.timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self.transport = transport or HttpxWhatsAppCloudValidationHttpTransport()
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()

    def validate(self, account: IntegrationAccount) -> ChannelConnectionValidationResult:
        performed = ["provider_type", "phone_number_id_configured"]
        passed: list[str] = []
        if account.provider != WHATSAPP_CLOUD_PROVIDER:
            return self._failure(
                account,
                "whatsapp_cloud_provider_mismatch",
                performed=performed,
                failed=("provider_type",),
                reconnect_eligible=False,
            )
        passed.append("provider_type")
        phone_number_id = (account.external_account_id or "").strip()
        if not phone_number_id:
            return self._failure(
                account,
                "whatsapp_cloud_phone_number_id_missing",
                performed=performed,
                passed=passed,
                failed=("phone_number_id_configured",),
            )
        passed.append("phone_number_id_configured")

        performed.append("provider_endpoint_configuration")
        if not self._configuration_is_allowlisted():
            return self._failure(
                account,
                "whatsapp_cloud_validator_configuration_invalid",
                performed=performed,
                passed=passed,
                failed=("provider_endpoint_configuration",),
                reconnect_eligible=False,
            )
        passed.append("provider_endpoint_configuration")

        performed.extend(("api_access_token_reference", "api_access_token_current"))
        try:
            reference = self.credential_reference_service.get_for_integration_account(
                account,
                API_ACCESS_TOKEN_PURPOSE,
            )
        except IntegrationCredentialReferenceNotFoundError:
            return self._failure(
                account,
                "whatsapp_cloud_access_token_reference_missing",
                performed=performed,
                passed=passed,
                failed=("api_access_token_reference",),
            )
        passed.append("api_access_token_reference")
        if self._is_expired(reference.expires_at):
            return self._failure(
                account,
                "whatsapp_cloud_access_token_expired",
                performed=performed,
                passed=passed,
                failed=("api_access_token_current",),
                authentication_failure=True,
            )
        passed.append("api_access_token_current")

        performed.append("api_access_token_resolved")
        try:
            access_token = self.secret_resolver.resolve(reference.secret_reference)
        except (KeyError, OSError, ValueError):
            access_token = None
        if not access_token:
            return self._failure(
                account,
                "whatsapp_cloud_access_token_unavailable",
                performed=performed,
                passed=passed,
                failed=("api_access_token_resolved",),
                authentication_failure=True,
            )
        passed.append("api_access_token_resolved")

        url = (
            f"{self.graph_api_base_url}/{self.graph_api_version}/"
            f"{quote(phone_number_id, safe='')}?fields=id,code_verification_status"
        )
        performed.extend(
            (
                "access_token_accepted",
                "phone_number_accessible",
                "provider_identity_matches",
                "phone_number_verified",
            )
        )
        try:
            response = self.transport.get(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            return self._failure(
                account,
                "whatsapp_cloud_network_error",
                performed=performed,
                passed=passed,
                failed=("access_token_accepted",),
                temporary=True,
            )

        error_code = self._provider_error_code(response.body)
        if response.status_code == 401 or error_code == 190:
            return self._failure(
                account,
                "whatsapp_cloud_authentication_failed",
                performed=performed,
                passed=passed,
                failed=("access_token_accepted",),
                authentication_failure=True,
            )
        if response.status_code == 403:
            return self._failure(
                account,
                "whatsapp_cloud_permission_denied",
                performed=performed,
                passed=passed + ["access_token_accepted"],
                failed=("phone_number_accessible",),
            )
        if response.status_code == 429:
            return self._failure(
                account,
                "whatsapp_cloud_rate_limited",
                performed=performed,
                passed=passed,
                failed=("access_token_accepted",),
                temporary=True,
            )
        if 500 <= response.status_code < 600:
            return self._failure(
                account,
                "whatsapp_cloud_provider_unavailable",
                performed=performed,
                passed=passed,
                failed=("access_token_accepted",),
                temporary=True,
            )
        if not 200 <= response.status_code < 300:
            return self._failure(
                account,
                "whatsapp_cloud_validation_failed",
                performed=performed,
                passed=passed,
                failed=("phone_number_accessible",),
            )

        passed.extend(("access_token_accepted", "phone_number_accessible"))
        provider_identity = self._provider_identity(response.body)
        if provider_identity != phone_number_id:
            return self._failure(
                account,
                "whatsapp_cloud_phone_number_id_mismatch",
                performed=performed,
                passed=passed,
                failed=("provider_identity_matches",),
                provider_identity=provider_identity,
            )
        passed.append("provider_identity_matches")
        if self._code_verification_status(response.body) != "VERIFIED":
            return self._failure(
                account,
                "whatsapp_cloud_phone_number_not_verified",
                performed=performed,
                passed=passed,
                failed=("phone_number_verified",),
                provider_identity=provider_identity,
            )
        passed.append("phone_number_verified")

        webhook_performed, webhook_passed, webhook_failed = self._local_webhook_checks(account)
        return ChannelConnectionValidationResult(
            succeeded=True,
            provider_account_identity=provider_identity,
            checks_performed=tuple(performed + webhook_performed),
            checks_passed=tuple(passed + webhook_passed),
            checks_failed=tuple(webhook_failed),
            checks_unavailable=_UNAVAILABLE_CHECKS,
        )

    def _local_webhook_checks(
        self,
        account: IntegrationAccount,
    ) -> tuple[list[str], list[str], list[str]]:
        performed: list[str] = []
        passed: list[str] = []
        failed: list[str] = []
        for purpose, check in (
            (WEBHOOK_APP_SECRET_PURPOSE, "local_webhook_app_secret_configured"),
            (WEBHOOK_VERIFY_TOKEN_PURPOSE, "local_webhook_verify_token_configured"),
        ):
            performed.append(check)
            try:
                self.credential_reference_service.get_for_integration_account(account, purpose)
            except IntegrationCredentialReferenceNotFoundError:
                failed.append(check)
            else:
                passed.append(check)
        return performed, passed, failed

    def _configuration_is_allowlisted(self) -> bool:
        parsed = urlparse(self.graph_api_base_url)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname == _GRAPH_API_HOST
            and parsed.port is None
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and _GRAPH_API_VERSION_PATTERN.fullmatch(self.graph_api_version)
            and self.graph_api_version in _ALLOWED_GRAPH_API_VERSIONS
        )

    @staticmethod
    def _is_expired(expires_at: datetime | None) -> bool:
        if expires_at is None:
            return False
        now = datetime.now(UTC)
        if expires_at.tzinfo is None:
            now = now.replace(tzinfo=None)
        return expires_at <= now

    @staticmethod
    def _provider_error_code(body: dict[str, Any] | None) -> int | None:
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return code if isinstance(code, int) else None

    @staticmethod
    def _provider_identity(body: dict[str, Any] | None) -> str | None:
        identity = body.get("id") if isinstance(body, dict) else None
        return identity.strip() if isinstance(identity, str) and identity.strip() else None

    @staticmethod
    def _code_verification_status(body: dict[str, Any] | None) -> str | None:
        status = body.get("code_verification_status") if isinstance(body, dict) else None
        return status.strip().upper() if isinstance(status, str) and status.strip() else None

    @staticmethod
    def _established_authorization(account: IntegrationAccount) -> bool:
        return bool(
            account.active
            or account.last_validated_at is not None
            or account.connection_status
            in {
                IntegrationAccountConnectionStatus.CONNECTED,
                IntegrationAccountConnectionStatus.RECONNECT_REQUIRED,
            }
        )

    def _failure(
        self,
        account: IntegrationAccount,
        reason_code: str,
        *,
        performed: list[str],
        passed: list[str] | None = None,
        failed: tuple[str, ...] = (),
        provider_identity: str | None = None,
        authentication_failure: bool = False,
        temporary: bool = False,
        reconnect_eligible: bool = True,
    ) -> ChannelConnectionValidationResult:
        return ChannelConnectionValidationResult(
            succeeded=False,
            reason_code=reason_code,
            reconnect_required=(
                not temporary
                and reconnect_eligible
                and self._established_authorization(account)
                and (authentication_failure or bool(failed))
            ),
            temporary_failure=temporary,
            provider_account_identity=provider_identity,
            checks_performed=tuple(performed),
            checks_passed=tuple(passed or ()),
            checks_failed=failed,
            checks_unavailable=_UNAVAILABLE_CHECKS,
        )


def default_channel_connection_validator_registry(
    session: Session,
    settings: Settings,
) -> ChannelConnectionValidatorRegistry:
    """Build the allowlisted validators implemented by HIRI today."""

    credential_service = IntegrationCredentialReferenceService(session)
    return ChannelConnectionValidatorRegistry(
        {
            WHATSAPP_CLOUD_PROVIDER: WhatsAppCloudConnectionValidator(
                credential_service,
                graph_api_base_url=settings.whatsapp_cloud_graph_api_base_url,
                graph_api_version=settings.whatsapp_cloud_graph_api_version,
                connect_timeout_seconds=settings.whatsapp_cloud_connect_timeout_seconds,
                read_timeout_seconds=settings.whatsapp_cloud_read_timeout_seconds,
            )
        }
    )
