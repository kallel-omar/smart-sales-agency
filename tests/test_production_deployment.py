import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runtime import RuntimeConfigurationError

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
PRODUCTION_DOC = ROOT / "docs" / "production-runtime.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"

SAFE_PRODUCTION_SECRET = "task296-safe-production-runtime-value"


def _settings(environment: str = "production", **overrides) -> Settings:
    values = {
        "environment": environment,
        "database_url": "sqlite://",
        "auth_token_secret": SAFE_PRODUCTION_SECRET,
        "api_docs_enabled": None,
        "cors_allowed_origins": "",
        "llm_mode": "demo",
        "outbound_webhook_url": "",
        "outbound_webhook_signing_enabled": True,
    }
    if environment == "production":
        values["database_url"] = "postgresql+psycopg://user:pw@db.example.test/app"
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_dockerfile_uses_safe_single_process_production_command():
    dockerfile = _read(DOCKERFILE)

    assert "python" in dockerfile
    assert "-m" in dockerfile
    assert "uvicorn" in dockerfile
    assert "app.main:app" in dockerfile
    assert "--host" in dockerfile
    assert "0.0.0.0" in dockerfile
    assert "--port" in dockerfile
    assert "8000" in dockerfile
    assert "--no-proxy-headers" in dockerfile
    assert "--reload" not in dockerfile
    assert "--workers" not in dockerfile
    assert "APP_ENV=production" in dockerfile


def test_dockerfile_runs_as_unprivileged_user_and_does_not_copy_secret_files():
    dockerfile = _read(DOCKERFILE)

    assert re.search(r"\bUSER\s+app\b", dockerfile)
    assert "useradd --system" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "COPY app ./app" in dockerfile
    assert "COPY pyproject.toml README.md alembic.ini ./" in dockerfile
    assert "COPY alembic ./alembic" in dockerfile
    assert "pip install --no-cache-dir ." in dockerfile
    assert "requirements.txt" not in dockerfile
    assert ".env" not in dockerfile
    assert "WHATSAPP_CLOUD_ACCESS_TOKEN" not in dockerfile
    assert "AUTH_TOKEN_SECRET=" not in dockerfile
    assert "Bearer " not in dockerfile


def test_dockerignore_excludes_secret_and_local_runtime_artifacts():
    dockerignore = _read(DOCKERIGNORE)

    required_patterns = [
        ".env",
        ".env.*",
        "!.env.example",
        "infra/n8n/.env",
        "infra/n8n/.env.*",
        "!infra/n8n/.env.example",
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "*.db",
        "*.sqlite",
        "*.sqlite3",
        "*.log",
    ]
    for pattern in required_patterns:
        assert pattern in dockerignore


def test_healthcheck_is_local_bounded_and_provider_free():
    dockerfile = _read(DOCKERFILE)

    assert "HEALTHCHECK" in dockerfile
    assert "--interval=30s" in dockerfile
    assert "--timeout=5s" in dockerfile
    assert "--start-period=10s" in dockerfile
    assert "--retries=3" in dockerfile
    assert "127.0.0.1:8000/health" in dockerfile
    assert "timeout=3" in dockerfile
    assert "/metrics" not in dockerfile
    for forbidden in ("graph.facebook.com", "cloudflare", "n8n", "openai"):
        assert forbidden not in dockerfile.lower()


def test_startup_failure_stays_fail_fast_and_does_not_echo_secret():
    unsafe_value = "task296-do-not-echo-this-value"

    with pytest.raises(RuntimeConfigurationError) as exc_info:
        create_app(_settings(auth_token_secret=unsafe_value))

    message = str(exc_info.value)
    assert "AUTH_TOKEN_SECRET" in message
    assert unsafe_value not in message
    assert "os.environ" not in message
    assert "Settings(" not in message


def test_lifespan_startup_and_shutdown_remain_clean(monkeypatch):
    calls = {"startup": 0}

    def fake_create_db_and_tables() -> None:
        calls["startup"] += 1

    monkeypatch.setattr("app.main.create_db_and_tables", fake_create_db_and_tables)
    local_app = create_app(
        _settings(
            "development",
            auth_token_secret="",
            outbound_webhook_signing_enabled=False,
        )
    )

    with TestClient(local_app) as client:
        assert client.get("/health").status_code == 200

    assert calls == {"startup": 1}


def test_operations_docs_capture_task296_deployment_contract():
    document = _read(PRODUCTION_DOC)

    required_snippets = [
        "docker build -t smart-sales-agency-api:local .",
        "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers",
        "Do not use `--reload` in production.",
        "unprivileged `app` user",
        "GET http://127.0.0.1:8000/health",
        "WHATSAPP_CLOUD_ACCESS_TOKEN",
        "FastAPI does not require or own `WHATSAPP_CLOUD_ACCESS_TOKEN`",
        "Multiple containers or workers would each have independent buckets.",
        "Network-level protection for metrics is a deployment concern",
        "PostgreSQL is required in production.",
        "alembic upgrade head",
        "application startup does not run `create_all`",
        "python -m app.migration_state check",
        "FastAPI never runs `alembic upgrade head` automatically.",
        "Do not edit an already applied\ncommitted revision.",
        "Backup, restore, point-in-time recovery, archival, and advanced migration",
        "No Sentry, OpenTelemetry exporter, or vendor SDK is required",
    ]
    for snippet in required_snippets:
        assert snippet in document
    assert "task295-manual-safe-auth-value-32-bytes" not in document
    assert "task296-safe-production-runtime-value" not in document


def test_ci_builds_production_docker_image_without_external_credentials():
    workflow = _read(CI_WORKFLOW)

    assert "docker build -t smart-sales-agency:test ." in workflow
    assert "WHATSAPP_CLOUD_ACCESS_TOKEN" not in workflow
    assert "AUTH_TOKEN_SECRET" not in workflow
    assert "docker push" not in workflow
    assert "sentry" not in workflow.lower()
