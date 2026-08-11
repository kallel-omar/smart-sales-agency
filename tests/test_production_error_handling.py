import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from sqlmodel import select

from app.api.dependencies import get_settings
from app.config import Settings
from app.db import get_session
from app.main import app
from app.models import AIInvocationStatus, AIInvocationUsage
from app.observability import (
    INTERNAL_SERVER_ERROR_DETAIL,
    REQUEST_ID_HEADER,
    HttpMetrics,
    RequestObservabilityMiddleware,
    safe_internal_server_error_body,
)
from app.services.rate_limiting import InMemoryFixedWindowRateLimitBackend

SENSITIVE_MARKERS = (
    "password=super-secret-test-value",
    "Authorization: Bearer fake-secret-token",
    "postgresql://test-user:test-password@example/db",
    "provider_api_key=fake-provider-secret",
    "Traceback",
    "RuntimeError",
    "C:\\internal\\service.py",
    "customer-message-task294-secret",
    "fake-webhook-signature-task294",
)


def _completion_records(caplog):
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http_request_completed"
    ]


def _structured_logs(caplog) -> str:
    return "\n".join(
        json.dumps(getattr(record, "structured_fields"), sort_keys=True)
        for record in caplog.records
        if hasattr(record, "structured_fields")
    )


def _sample_value(metrics_text: str, sample_name: str, labels: dict[str, str]) -> float:
    for family in text_string_to_metric_families(metrics_text):
        for sample in family.samples:
            if sample.name == sample_name and dict(sample.labels) == labels:
                return float(sample.value)
    return 0.0


def _assert_markers_absent(serialized: str, markers=SENSITIVE_MARKERS) -> None:
    for marker in markers:
        assert marker not in serialized


def _session_rows(model):
    with next(app.dependency_overrides[get_session]()) as session:
        return list(session.exec(select(model)).all())


def _workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def test_unexpected_exception_returns_safe_500_with_request_id_logs_and_metrics(caplog):
    caplog.set_level(logging.INFO)
    metrics = HttpMetrics()
    local_app = FastAPI()

    @local_app.get("/boom/{item_id}")
    def boom(item_id: str) -> dict[str, str]:
        assert item_id
        raise RuntimeError(
            "password=super-secret-test-value "
            "Authorization: Bearer fake-secret-token "
            "postgresql://test-user:test-password@example/db "
            "provider_api_key=fake-provider-secret "
            "Traceback C:\\internal\\service.py"
        )

    local_app.add_middleware(RequestObservabilityMiddleware, metrics=metrics)

    with TestClient(local_app, raise_server_exceptions=False) as client:
        response = client.get(
            "/boom/concrete-sensitive-id",
            headers={REQUEST_ID_HEADER: "task294-500"},
        )

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "task294-500"
    assert response.json() == {
        "detail": INTERNAL_SERVER_ERROR_DETAIL,
        "request_id": "task294-500",
    }
    _assert_markers_absent(response.text)
    assert "concrete-sensitive-id" not in response.text

    completion_records = _completion_records(caplog)
    assert len(completion_records) == 1
    completion = completion_records[0].structured_fields
    assert completion["request_id"] == "task294-500"
    assert completion["route"] == "/boom/{item_id}"
    assert completion["status_code"] == 500
    assert completion["duration_ms"] >= 0

    logs = _structured_logs(caplog)
    assert "internal_server_error" in logs
    assert "RuntimeError" in logs
    _assert_markers_absent(
        logs,
        (marker for marker in SENSITIVE_MARKERS if marker != "RuntimeError"),
    )
    assert "concrete-sensitive-id" not in logs

    metrics_text = metrics.render_latest().decode()
    assert _sample_value(
        metrics_text,
        "http_requests_total",
        {"method": "GET", "route": "/boom/{item_id}", "status_code": "500"},
    ) == 1.0
    _assert_markers_absent(metrics_text)
    assert "concrete-sensitive-id" not in metrics_text


def test_safe_internal_error_body_cannot_include_exception_material():
    body = safe_internal_server_error_body("task294-safe-body")

    assert body == {
        "detail": "Internal server error",
        "request_id": "task294-safe-body",
    }
    assert set(body) == {"detail", "request_id"}


def test_validation_errors_strip_submitted_secret_inputs(client):
    response = client.post(
        "/api/auth/login",
        headers={REQUEST_ID_HEADER: "task294-validation"},
        json={
            "email": "fixture-operator@example.com",
            "password": {"token": "password=super-secret-test-value"},
        },
    )

    assert response.status_code == 422
    assert response.headers[REQUEST_ID_HEADER] == "task294-validation"
    assert response.json()["detail"][0]["loc"] == ["body", "password"]
    assert "input" not in response.json()["detail"][0]
    assert "ctx" not in response.json()["detail"][0]
    _assert_markers_absent(response.text)
    assert "super-secret-test-value" not in response.text


def test_auth_and_machine_integration_failures_remain_safe_and_separate(client):
    bearer_token = "fake-secret-token"
    bearer = client.get(
        "/api/auth/me",
        headers={
            "Authorization": f"Bearer {bearer_token}",
            REQUEST_ID_HEADER: "task294-bearer",
        },
    )
    webhook = client.post(
        "/api/integrations/inbound-events",
        headers={
            "X-Integration-Key": "fake-machine-credential-task294",
            "X-Webhook-Signature": "fake-webhook-signature-task294",
            "X-Webhook-Timestamp": "1",
            "Authorization": f"Bearer {bearer_token}",
            REQUEST_ID_HEADER: "task294-webhook",
        },
        json={
            "lead_id": str(uuid4()),
            "channel": "console",
            "content": "customer-message-task294-secret",
        },
    )

    assert bearer.status_code == 401
    assert bearer.json()["detail"] == "Invalid bearer authentication"
    assert bearer_token not in bearer.text
    assert webhook.status_code == 401
    assert webhook.json()["detail"] == "Invalid webhook authentication"
    _assert_markers_absent(webhook.text)
    assert "fake-machine-credential-task294" not in webhook.text


def test_known_404_workspace_isolation_and_409_conflict_semantics_remain(client):
    assert client.post(
        "/api/workspaces",
        json={"slug": "task294-a", "name": "Task 294 A"},
    ).status_code == 201
    assert client.post(
        "/api/workspaces",
        json={"slug": "task294-b", "name": "Task 294 B"},
    ).status_code == 201
    duplicate = client.post(
        "/api/workspaces",
        json={"slug": "task294-a", "name": "Task 294 A Again"},
    )
    lead = client.post(
        "/api/leads",
        headers=_workspace_headers("task294-a"),
        json={
            "tenant_id": "task294-a",
            "full_name": "Task 294 Lead",
            "company_name": "Task 294 Company",
            "source": "manual",
        },
    )
    hidden = client.get(
        f"/api/leads/{lead.json()['id']}",
        headers=_workspace_headers("task294-b"),
    )
    missing = client.get(
        f"/api/leads/{uuid4()}",
        headers=_workspace_headers("task294-a"),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "A workspace with this slug already exists"
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "Lead not found"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Lead not found"


def test_task293_rate_limit_response_and_headers_are_preserved(client):
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
        json={"email": "fixture-operator@example.com", "password": "fixture-password"},
    )
    limited = client.post(
        "/api/auth/login",
        headers={REQUEST_ID_HEADER: "task294-rate-limit"},
        json={"email": "fixture-operator@example.com", "password": "fixture-password"},
    )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.json()["detail"] == "Rate limit exceeded"
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.headers["X-RateLimit-Limit"] == "1"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert "X-RateLimit-Reset" in limited.headers
    assert limited.headers[REQUEST_ID_HEADER] == "task294-rate-limit"


def test_ai_provider_failure_records_failed_usage_and_returns_safe_response(client, monkeypatch):
    settings = app.dependency_overrides[get_settings]().model_copy(
        update={
            "llm_mode": "openai_compatible",
            "llm_api_key": "synthetic-ai-key",
            "ai_model_tier_mappings": {
                "standard": {"provider": "provider-b", "model": "standard-model"}
            },
        }
    )
    app.dependency_overrides[get_settings] = lambda: settings
    workspace = client.post(
        "/api/workspaces",
        json={"slug": "task294-ai", "name": "Task 294 AI"},
    )
    lead = client.post(
        "/api/leads",
        headers=_workspace_headers("task294-ai"),
        json={
            "tenant_id": "task294-ai",
            "full_name": "AI Failure Lead",
            "company_name": "AI Failure Company",
            "source": "manual",
        },
    )

    async def fail_after_accounting(self, request):
        self._usage_service.record(
            request.workspace,
            task_identifier=request.task_identifier,
            agent_identifier=request.agent_identifier,
            provider="provider-b",
            model="standard-model",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            latency_ms=1,
            status=AIInvocationStatus.FAILED,
            conversation_id=request.conversation_id,
        )
        raise RuntimeError(
            "provider_api_key=fake-provider-secret "
            "Authorization: Bearer fake-secret-token"
        )

    monkeypatch.setattr(
        "app.services.ai_invocation_gateway.AIInvocationGateway.invoke",
        fail_after_accounting,
    )
    before = len(_session_rows(AIInvocationUsage))
    with TestClient(
        app,
        raise_server_exceptions=False,
        headers=dict(client.headers),
    ) as safe_client:
        response = safe_client.post(
            f"/api/conversations/{lead.json()['id']}/reply",
            headers={
                **_workspace_headers("task294-ai"),
                REQUEST_ID_HEADER: "task294-ai-provider",
            },
            json={"channel": "console", "content": "How much does it cost?"},
        )
    rows = _session_rows(AIInvocationUsage)
    failed_rows = [row for row in rows if row.status is AIInvocationStatus.FAILED]

    assert workspace.status_code == 201
    assert lead.status_code == 201
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal server error",
        "request_id": "task294-ai-provider",
    }
    _assert_markers_absent(response.text)
    assert len(rows) == before + 1
    assert len(failed_rows) == 1
    assert failed_rows[0].conversation_id == UUID(lead.json()["id"])


def test_database_style_error_text_is_not_returned_publicly():
    local_app = FastAPI()

    @local_app.get("/db")
    def db_failure() -> dict[str, bool]:
        raise RuntimeError(
            "SQL SELECT * FROM users WHERE password='super-secret-test-value' "
            "postgresql://test-user:test-password@example/db"
        )

    local_app.add_middleware(RequestObservabilityMiddleware)

    with TestClient(local_app, raise_server_exceptions=False) as client:
        response = client.get("/db", headers={REQUEST_ID_HEADER: "task294-db"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert response.json()["request_id"] == "task294-db"
    _assert_markers_absent(response.text)
    assert "SQL" not in response.text


def test_readiness_degraded_response_remains_safe(client):
    workspace = client.post(
        "/api/workspaces",
        json={"slug": "task294-ready", "name": "Task 294 Ready"},
    )
    account = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers("task294-ready"),
        json={
            "provider": "generic_hmac",
            "external_account_id": "task294-ready-account",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    response = client.get(
        f"/api/integrations/accounts/{account.json()['id']}/health/runtime-readiness",
        headers=_workspace_headers("task294-ready"),
    )

    assert workspace.status_code == 201
    assert account.status_code == 201
    assert response.status_code == 200
    serialized = response.text
    assert "test-generic-hmac-secret" not in serialized
    assert "inbound_credential" not in serialized
    assert "Bearer " not in serialized
    assert "Traceback" not in serialized


def test_no_route_local_broad_exception_catch_was_added():
    route_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path(__file__).resolve().parents[1] / "app" / "api" / "routes").glob("*.py")
    )

    assert "except Exception" not in route_source
