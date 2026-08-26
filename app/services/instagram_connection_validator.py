"""Read-only native Instagram Login connection validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx

from app.integrations.providers import (
    API_ACCESS_TOKEN_PURPOSE,
    INSTAGRAM_DM_PROVIDER,
    INSTAGRAM_LOGIN_AUTH_MODE,
    WEBHOOK_APP_SECRET_PURPOSE,
    WEBHOOK_VERIFY_TOKEN_PURPOSE,
)
from app.models import IntegrationAccount, IntegrationAccountConnectionStatus
from app.services.channel_connections import ChannelConnectionValidationResult
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceNotFoundError,
    IntegrationCredentialReferenceService,
)
from app.services.secret_resolver import EnvironmentSecretResolver, SecretResolver

_GRAPH_API_HOST = "graph.instagram.com"
_ALLOWED_GRAPH_API_VERSIONS = frozenset({"v23.0"})
_GRAPH_API_VERSION_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+$")
_UNAVAILABLE_CHECKS = (
    "provider_webhook_subscription",
    "provider_webhook_subscription_fields",
)


@dataclass(frozen=True)
class InstagramValidationHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: dict[str, Any] | None = None


class InstagramValidationHttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> InstagramValidationHttpResponse: ...


class HttpxInstagramValidationHttpTransport:
    """HTTP boundary for native Instagram Login read-only checks."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> InstagramValidationHttpResponse:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)

        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = None
        return InstagramValidationHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body,
        )


class InstagramNativeLoginConnectionValidator:
    """Validate one native Instagram professional account without mutation."""

    def __init__(
        self,
        credential_reference_service: IntegrationCredentialReferenceService,
        *,
        graph_api_base_url: str,
        graph_api_version: str,
        connect_timeout_seconds: float = 5,
        read_timeout_seconds: float = 15,
        transport: InstagramValidationHttpTransport | None = None,
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
        self.transport = transport or HttpxInstagramValidationHttpTransport()
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()

    def validate(self, account: IntegrationAccount) -> ChannelConnectionValidationResult:
        performed = [
            "provider_type",
            "provider_auth_mode",
            "instagram_account_id_configured",
        ]
        passed: list[str] = []
        if account.provider != INSTAGRAM_DM_PROVIDER:
            return self._failure(
                account,
                "instagram_native_provider_mismatch",
                performed=performed,
                failed=("provider_type",),
                reconnect_eligible=False,
            )
        passed.append("provider_type")
        if account.provider_auth_mode != INSTAGRAM_LOGIN_AUTH_MODE:
            return self._failure(
                account,
                "instagram_native_auth_mode_mismatch",
                performed=performed,
                passed=passed,
                failed=("provider_auth_mode",),
                reconnect_eligible=False,
            )
        passed.append("provider_auth_mode")

        account_id = (account.external_account_id or "").strip()
        if not account_id:
            return self._failure(
                account,
                "instagram_native_account_id_missing",
                performed=performed,
                passed=passed,
                failed=("instagram_account_id_configured",),
            )
        passed.append("instagram_account_id_configured")

        performed.append("provider_endpoint_configuration")
        if not self._configuration_is_allowlisted():
            return self._failure(
                account,
                "instagram_native_validator_configuration_invalid",
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
                "instagram_native_access_token_reference_missing",
                performed=performed,
                passed=passed,
                failed=("api_access_token_reference",),
            )
        passed.append("api_access_token_reference")
        if self._is_expired(reference.expires_at):
            return self._failure(
                account,
                "instagram_native_access_token_expired",
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
                "instagram_native_access_token_unavailable",
                performed=performed,
                passed=passed,
                failed=("api_access_token_resolved",),
                authentication_failure=True,
            )
        passed.append("api_access_token_resolved")

        request_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        identity_url = (
            f"{self.graph_api_base_url}/{self.graph_api_version}/me"
            "?fields=user_id,username"
        )
        performed.extend(
            (
                "access_token_accepted",
                "instagram_business_basic_access",
                "provider_identity_matches",
                "professional_account_username_available",
            )
        )
        try:
            identity_response = self.transport.get(
                identity_url,
                headers=request_headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            return self._failure(
                account,
                "instagram_native_network_error",
                performed=performed,
                passed=passed,
                failed=("access_token_accepted",),
                temporary=True,
            )

        response_failure = self._response_failure(
            account,
            identity_response,
            performed=performed,
            passed=passed,
            permission_reason="instagram_native_basic_permission_denied",
            permission_check="instagram_business_basic_access",
            general_reason="instagram_native_identity_validation_failed",
            general_check="instagram_business_basic_access",
            authentication_check="access_token_accepted",
        )
        if response_failure is not None:
            return response_failure

        passed.extend(("access_token_accepted", "instagram_business_basic_access"))
        provider_identity, username = self._identity(identity_response.body)
        if provider_identity != account_id:
            return self._failure(
                account,
                "instagram_native_account_id_mismatch",
                performed=performed,
                passed=passed,
                failed=("provider_identity_matches",),
                provider_identity=provider_identity,
            )
        passed.append("provider_identity_matches")
        username_missing = not username
        if not username_missing:
            passed.append("professional_account_username_available")

        performed.append("instagram_business_manage_messages_access")
        conversations_url = (
            f"{self.graph_api_base_url}/{self.graph_api_version}/"
            f"{quote(provider_identity, safe='')}/conversations?platform=instagram"
        )
        try:
            messaging_response = self.transport.get(
                conversations_url,
                headers=request_headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            return self._failure(
                account,
                "instagram_native_network_error",
                performed=performed,
                passed=passed,
                failed=("instagram_business_manage_messages_access",),
                provider_identity=provider_identity,
                temporary=True,
            )

        response_failure = self._response_failure(
            account,
            messaging_response,
            performed=performed,
            passed=passed,
            permission_reason="instagram_native_messaging_permission_denied",
            permission_check="instagram_business_manage_messages_access",
            general_reason="instagram_native_messaging_access_failed",
            general_check="instagram_business_manage_messages_access",
            authentication_check="instagram_business_manage_messages_access",
            provider_identity=provider_identity,
        )
        if response_failure is not None:
            return response_failure
        passed.append("instagram_business_manage_messages_access")

        webhook_performed, webhook_passed, webhook_failed = self._local_webhook_checks(
            account
        )
        checks_failed = list(webhook_failed)
        if username_missing:
            checks_failed.insert(0, "professional_account_username_available")
        return ChannelConnectionValidationResult(
            succeeded=True,
            provider_account_identity=provider_identity,
            checks_performed=tuple(performed + webhook_performed),
            checks_passed=tuple(passed + webhook_passed),
            checks_failed=tuple(checks_failed),
            checks_unavailable=_UNAVAILABLE_CHECKS,
        )

    def _response_failure(
        self,
        account: IntegrationAccount,
        response: InstagramValidationHttpResponse,
        *,
        performed: list[str],
        passed: list[str],
        permission_reason: str,
        permission_check: str,
        general_reason: str,
        general_check: str,
        authentication_check: str,
        provider_identity: str | None = None,
    ) -> ChannelConnectionValidationResult | None:
        error_code = self._provider_error_code(response.body)
        if response.status_code == 401 or error_code == 190:
            return self._failure(
                account,
                "instagram_native_authentication_failed",
                performed=performed,
                passed=passed,
                failed=(authentication_check,),
                provider_identity=provider_identity,
                authentication_failure=True,
            )
        if response.status_code == 403:
            return self._failure(
                account,
                permission_reason,
                performed=performed,
                passed=passed,
                failed=(permission_check,),
                provider_identity=provider_identity,
            )
        if response.status_code == 429:
            return self._failure(
                account,
                "instagram_native_rate_limited",
                performed=performed,
                passed=passed,
                failed=(general_check,),
                provider_identity=provider_identity,
                temporary=True,
            )
        if 500 <= response.status_code < 600:
            return self._failure(
                account,
                "instagram_native_provider_unavailable",
                performed=performed,
                passed=passed,
                failed=(general_check,),
                provider_identity=provider_identity,
                temporary=True,
            )
        if not 200 <= response.status_code < 300:
            return self._failure(
                account,
                general_reason,
                performed=performed,
                passed=passed,
                failed=(general_check,),
                provider_identity=provider_identity,
            )
        return None

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
                self.credential_reference_service.get_for_integration_account(
                    account,
                    purpose,
                )
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
    def _identity(body: dict[str, Any] | None) -> tuple[str | None, str | None]:
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            return None, None
        identity = data[0].get("user_id")
        username = data[0].get("username")
        return (
            identity.strip() if isinstance(identity, str) and identity.strip() else None,
            username.strip() if isinstance(username, str) and username.strip() else None,
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
