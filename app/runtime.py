"""Runtime startup policy for the FastAPI application."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_network
from urllib.parse import urlsplit

from app.config import Settings
from app.database_urls import DatabaseURLConfigurationError, is_postgresql_database_url


class RuntimeConfigurationError(RuntimeError):
    """Raised when runtime configuration is unsafe or malformed."""


@dataclass(frozen=True)
class RuntimePolicy:
    api_docs_enabled: bool
    cors_allowed_origins: tuple[str, ...]
    cors_allow_credentials: bool
    trusted_proxy_hosts: tuple[str, ...]


class ProductionRuntimeValidator:
    """Validate process-level runtime configuration before serving traffic."""

    _UNSAFE_AUTH_SECRETS = frozenset(
        {
            "",
            "change-me",
            "changeme",
            "development",
            "development-secret",
            "dev-secret",
            "replace-with-a-secret",
            "replace-with-a-machine-secret",
            "secret",
            "test",
            "test-secret",
            "test-auth-token-secret-32-byte-value",
        }
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate(self) -> RuntimePolicy:
        policy = runtime_policy_from_settings(self.settings)
        self._validate_database_url()
        self._validate_configured_urls()

        if self.settings.environment == "production":
            self._validate_production_secret()
            self._validate_production_database()
            self._validate_production_outbound_signing()

        return policy

    def _validate_production_secret(self) -> None:
        secret = self.settings.auth_token_secret.get_secret_value()
        if len(secret) < 32 or secret.strip().lower() in self._UNSAFE_AUTH_SECRETS:
            raise RuntimeConfigurationError("AUTH_TOKEN_SECRET is not production-safe")

    def _validate_database_url(self) -> None:
        try:
            is_postgresql_database_url(self.settings.database_url)
        except DatabaseURLConfigurationError as exc:
            raise RuntimeConfigurationError("DATABASE_URL is malformed") from exc

    def _validate_production_database(self) -> None:
        if not is_postgresql_database_url(self.settings.database_url):
            raise RuntimeConfigurationError("DATABASE_URL must use PostgreSQL in production")

    def _validate_production_outbound_signing(self) -> None:
        if (
            self.settings.outbound_webhook_url.strip()
            and not self.settings.outbound_webhook_signing_enabled
        ):
            raise RuntimeConfigurationError(
                "OUTBOUND_WEBHOOK_SIGNING_ENABLED must be true when "
                "OUTBOUND_WEBHOOK_URL is configured in production"
            )

    def _validate_configured_urls(self) -> None:
        if self.settings.outbound_webhook_url.strip():
            _parse_http_url(
                self.settings.outbound_webhook_url,
                setting_name="OUTBOUND_WEBHOOK_URL",
            )
        if self.settings.llm_mode == "openai_compatible":
            _parse_http_url(
                self.settings.llm_base_url,
                setting_name="LLM_BASE_URL",
            )


def runtime_policy_from_settings(settings: Settings) -> RuntimePolicy:
    return RuntimePolicy(
        api_docs_enabled=_effective_api_docs_enabled(settings),
        cors_allowed_origins=_parse_cors_allowed_origins(settings),
        cors_allow_credentials=settings.cors_allow_credentials,
        trusted_proxy_hosts=_parse_trusted_proxy_hosts(settings),
    )


def _effective_api_docs_enabled(settings: Settings) -> bool:
    if settings.api_docs_enabled is not None:
        return settings.api_docs_enabled
    return settings.environment != "production"


def _parse_cors_allowed_origins(settings: Settings) -> tuple[str, ...]:
    origins: list[str] = []
    seen: set[str] = set()
    for raw_origin in _split_csv(settings.cors_allowed_origins):
        if raw_origin == "*":
            if settings.cors_allow_credentials:
                raise RuntimeConfigurationError(
                    "CORS wildcard origin cannot be used with credentials"
                )
            if settings.environment == "production":
                raise RuntimeConfigurationError(
                    "CORS wildcard origin is not allowed in production"
                )
            normalized = raw_origin
        else:
            normalized = _normalize_cors_origin(
                raw_origin,
                production=settings.environment == "production",
            )
        if normalized not in seen:
            origins.append(normalized)
            seen.add(normalized)
    return tuple(origins)


def _parse_trusted_proxy_hosts(settings: Settings) -> tuple[str, ...]:
    hosts: list[str] = []
    seen: set[str] = set()
    for raw_host in _split_csv(settings.trusted_proxy_hosts):
        if raw_host == "*":
            raise RuntimeConfigurationError("TRUSTED_PROXY_HOSTS must not trust all sources")
        try:
            normalized = str(ip_network(raw_host, strict=False))
        except ValueError as exc:
            raise RuntimeConfigurationError(
                "TRUSTED_PROXY_HOSTS must contain only IP addresses or CIDR ranges"
            ) from exc
        if normalized not in seen:
            hosts.append(normalized)
            seen.add(normalized)
    return tuple(hosts)


def _normalize_cors_origin(origin: str, *, production: bool) -> str:
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeConfigurationError(
            "CORS_ALLOWED_ORIGINS must contain absolute http or https origins"
        )
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise RuntimeConfigurationError(
            "CORS_ALLOWED_ORIGINS must contain origins only, not credentials, paths, or queries"
        )
    if production and parsed.scheme != "https":
        raise RuntimeConfigurationError(
            "CORS_ALLOWED_ORIGINS must use https origins in production"
        )
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _parse_http_url(value: str, *, setting_name: str) -> None:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeConfigurationError(
            f"{setting_name} must be an absolute http or https URL"
        )
    if parsed.username or parsed.password or parsed.fragment:
        raise RuntimeConfigurationError(
            f"{setting_name} must not contain credentials or fragments"
        )


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())
