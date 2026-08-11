import socket

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import get_settings
from app.config import Settings
from app.main import app, create_app
from app.runtime import ProductionRuntimeValidator, RuntimeConfigurationError
from app.services.rate_limiting import InMemoryFixedWindowRateLimitBackend

SAFE_PRODUCTION_SECRET = "task295-production-token-secret-value"


def _settings(environment: str = "development", **overrides) -> Settings:
    values = {
        "environment": environment,
        "database_url": "sqlite://",
        "auth_token_secret": "",
        "outbound_webhook_url": "",
        "outbound_webhook_signing_enabled": False,
        "llm_mode": "demo",
        "cors_allowed_origins": "",
        "trusted_proxy_hosts": "",
        "api_docs_enabled": None,
    }
    if environment == "production":
        values["auth_token_secret"] = SAFE_PRODUCTION_SECRET
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _disable_db_startup(monkeypatch) -> dict[str, int]:
    calls = {"count": 0}

    def fake_create_db_and_tables() -> None:
        calls["count"] += 1

    monkeypatch.setattr("app.main.create_db_and_tables", fake_create_db_and_tables)
    return calls


def test_development_and_test_modes_start_with_local_docs_defaults(monkeypatch):
    db_calls = _disable_db_startup(monkeypatch)

    for environment in ("development", "test"):
        local_app = create_app(_settings(environment))
        with TestClient(local_app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/docs").status_code == 200
            assert client.get("/openapi.json").status_code == 200

    assert db_calls["count"] == 2


def test_valid_production_starts_and_disables_docs_by_default(monkeypatch):
    db_calls = _disable_db_startup(monkeypatch)
    local_app = create_app(
        _settings(
            "production",
            cors_allowed_origins="https://app.example.test",
        )
    )

    with TestClient(local_app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404

    assert db_calls["count"] == 1


def test_production_docs_can_be_enabled_explicitly(monkeypatch):
    _disable_db_startup(monkeypatch)
    local_app = create_app(
        _settings(
            "production",
            api_docs_enabled=True,
            cors_allowed_origins="https://app.example.test",
        )
    )

    with TestClient(local_app) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_invalid_app_env_is_rejected(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_production_unsafe_auth_secret_fails_without_echoing_value():
    unsafe_value = "task295-unsafe-runtime-value"
    settings = _settings("production", auth_token_secret=unsafe_value)

    with pytest.raises(RuntimeConfigurationError) as exc_info:
        create_app(settings)

    message = str(exc_info.value)
    assert "AUTH_TOKEN_SECRET" in message
    assert unsafe_value not in message


def test_production_outbound_webhook_requires_signing_without_exposing_url():
    unsafe_url = "https://transport.example.test/webhook/task295"
    settings = _settings(
        "production",
        outbound_webhook_url=unsafe_url,
        outbound_webhook_signing_enabled=False,
        cors_allowed_origins="https://app.example.test",
    )

    with pytest.raises(RuntimeConfigurationError) as exc_info:
        create_app(settings)

    message = str(exc_info.value)
    assert "OUTBOUND_WEBHOOK_SIGNING_ENABLED" in message
    assert unsafe_url not in message


def test_cors_allows_only_configured_static_origin(monkeypatch):
    _disable_db_startup(monkeypatch)
    allowed_origin = "https://app.example.test"
    local_app = create_app(
        _settings(
            "production",
            cors_allowed_origins=f" {allowed_origin} , https://ops.example.test ",
            cors_allow_credentials=True,
        )
    )

    with TestClient(local_app) as client:
        allowed = client.options(
            "/health",
            headers={
                "Origin": allowed_origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        disallowed = client.options(
            "/health",
            headers={
                "Origin": "https://other.example.test",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == allowed_origin
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in disallowed.headers


@pytest.mark.parametrize(
    "settings",
    [
        _settings("production", cors_allowed_origins="*"),
        _settings(
            "development",
            cors_allowed_origins="*",
            cors_allow_credentials=True,
        ),
        _settings("development", cors_allowed_origins="https://app.example.test/path"),
        _settings("development", cors_allowed_origins="not-an-origin"),
    ],
)
def test_unsafe_or_malformed_cors_configuration_is_rejected(settings):
    with pytest.raises(RuntimeConfigurationError):
        ProductionRuntimeValidator(settings).validate()


def test_trusted_proxy_policy_is_explicit_bounded_and_not_applied_to_requests(client):
    policy = ProductionRuntimeValidator(
        _settings("production", trusted_proxy_hosts="10.0.0.1,10.0.0.0/24")
    ).validate()
    assert policy.trusted_proxy_hosts == ("10.0.0.1/32", "10.0.0.0/24")

    with pytest.raises(RuntimeConfigurationError):
        ProductionRuntimeValidator(_settings("production", trusted_proxy_hosts="*")).validate()

    limited_settings = app.dependency_overrides[get_settings]().model_copy(
        update={
            "rate_limit_enabled": True,
            "rate_limit_auth_login_limit": 1,
            "rate_limit_auth_login_window_seconds": 60,
        }
    )
    app.dependency_overrides[get_settings] = lambda: limited_settings
    app.state.rate_limit_backend = InMemoryFixedWindowRateLimitBackend()

    first = client.post(
        "/api/auth/login",
        headers={
            "X-Forwarded-For": "203.0.113.10",
            "CF-Connecting-IP": "203.0.113.11",
        },
        json={"email": "fixture-operator@example.com", "password": "fixture-password"},
    )
    second = client.post(
        "/api/auth/login",
        headers={
            "X-Forwarded-For": "198.51.100.10",
            "CF-Connecting-IP": "198.51.100.11",
        },
        json={"email": "fixture-operator@example.com", "password": "fixture-password"},
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_production_unexpected_errors_remain_generic(monkeypatch):
    _disable_db_startup(monkeypatch)
    local_app = create_app(
        _settings(
            "production",
            cors_allowed_origins="https://app.example.test",
        )
    )

    @local_app.get("/boom")
    def boom() -> dict[str, bool]:
        raise RuntimeError("task295-sensitive-runtime-detail")

    with TestClient(local_app, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"X-Request-ID": "task295-error"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal server error",
        "request_id": "task295-error",
    }
    assert "task295-sensitive-runtime-detail" not in response.text


def test_metrics_enable_disable_and_health_semantics_are_preserved(monkeypatch):
    _disable_db_startup(monkeypatch)
    enabled_app = create_app(_settings("production", metrics_enabled=True))
    disabled_app = create_app(_settings("production", metrics_enabled=False))

    with TestClient(enabled_app) as enabled_client:
        assert enabled_client.get("/health").json()["status"] == "ok"
        assert enabled_client.get("/metrics").status_code == 200

    with TestClient(disabled_app) as disabled_client:
        assert disabled_client.get("/health").json()["status"] == "ok"
        assert disabled_client.get("/metrics").status_code == 404


def test_startup_validation_is_local_and_does_not_probe_network(monkeypatch):
    _disable_db_startup(monkeypatch)

    def fail_network_probe(*args, **kwargs):
        raise AssertionError("startup validation must not open network connections")

    monkeypatch.setattr(socket, "create_connection", fail_network_probe)
    local_app = create_app(
        _settings(
            "production",
            outbound_webhook_url="https://transport.example.test/webhook/task295",
            outbound_webhook_signing_enabled=True,
            cors_allowed_origins="https://app.example.test",
        )
    )

    with TestClient(local_app) as client:
        assert client.get("/health").status_code == 200


def test_runtime_configuration_failure_does_not_dump_environment_or_secret():
    unsafe_value = "task295-do-not-echo-this-value"

    with pytest.raises(RuntimeConfigurationError) as exc_info:
        ProductionRuntimeValidator(
            _settings("production", auth_token_secret=unsafe_value)
        ).validate()

    message = str(exc_info.value)
    assert "AUTH_TOKEN_SECRET" in message
    assert unsafe_value not in message
    assert "DATABASE_URL" not in message
    assert "os.environ" not in message
