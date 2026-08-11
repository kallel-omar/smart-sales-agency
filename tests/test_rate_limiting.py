import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from sqlmodel import select

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import (
    AIInvocationUsage,
    ConversationMessage,
    InboundIntegrationEventReceipt,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationDeliveryAttempt,
    OutboundProviderDeliveryStatusEvent,
)
from app.observability import REQUEST_ID_HEADER
from app.services.rate_limiting import (
    InMemoryFixedWindowRateLimitBackend,
    RateLimitExceeded,
    RateLimitPolicy,
    RateLimitPolicyId,
    RateLimitService,
    rate_limit_headers,
)


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _test_settings(**overrides) -> Settings:
    values = {
        "environment": "test",
        "database_url": "sqlite://",
        "llm_mode": "demo",
        "require_human_approval": True,
        "auth_token_secret": "test-auth-token-secret-32-byte-value",
        "rate_limit_enabled": True,
        "rate_limit_auth_login_limit": 1_000,
        "rate_limit_integration_ingest_limit": 1_000,
        "rate_limit_outbound_delivery_limit": 1_000,
        "rate_limit_ai_conversation_limit": 1_000,
    }
    values.update(overrides)
    return Settings(**values)


def _install_rate_limit_settings(**overrides) -> InMemoryFixedWindowRateLimitBackend:
    settings = _test_settings(**overrides)
    backend = InMemoryFixedWindowRateLimitBackend()
    app.state.rate_limit_backend = backend
    app.dependency_overrides[get_settings] = lambda: settings
    return backend


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _create_workspace(client, slug: str) -> dict:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.title()})
    assert response.status_code == 201
    return response.json()


def _create_lead(client, workspace_slug: str) -> dict:
    response = client.post(
        "/api/leads",
        headers=_headers(workspace_slug),
        json={
            "tenant_id": workspace_slug,
            "full_name": f"Lead {workspace_slug}",
            "company_name": "Example",
            "email": f"{workspace_slug}@example.test",
            "source": "manual",
        },
    )
    assert response.status_code == 201
    return response.json()


def _provision_account(client, workspace_slug: str, credential: str | None = None) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=_headers(workspace_slug),
        json={
            "provider": "generic_hmac",
            "external_account_id": f"{workspace_slug}-account",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    if credential is None:
        return response.json()
    account = response.json()
    account["credential"] = account["inbound_credential"]
    return account


def _create_outbound_action(
    client,
    workspace_slug: str,
    account_id: str,
    *,
    requires_approval: bool = False,
    key: str = "task293-action",
) -> dict:
    response = client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions",
        headers=_headers(workspace_slug),
        json={
            "external_target_id": "recipient-task293",
            "action_type": "send_message",
            "content": "Task 293 test text",
            "payload": {"format": "plain_text"},
            "idempotency_key": key,
            "requires_approval": requires_approval,
        },
    )
    assert response.status_code == 201
    return response.json()


def _rows(model):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        return list(session.exec(select(model)).all())


def _sample_value(metrics_text: str, sample_name: str, labels: dict[str, str]) -> float:
    for family in text_string_to_metric_families(metrics_text):
        for sample in family.samples:
            if sample.name == sample_name and dict(sample.labels) == labels:
                return float(sample.value)
    return 0.0


def _post_signed(client, signed_webhook_request, credential: str, endpoint: str, payload: dict):
    headers, body = signed_webhook_request(
        credential,
        payload,
        event_id=f"task293-{uuid4()}",
    )
    return client.post(endpoint, headers=headers, content=body)


def test_fixed_window_limit_allows_exact_limit_denies_then_expires():
    clock = ManualClock()
    backend = InMemoryFixedWindowRateLimitBackend(clock=clock)
    service = RateLimitService(backend)
    policy = RateLimitPolicy(RateLimitPolicyId.AUTH_LOGIN, limit=2, window_seconds=10)

    first = service.check(policy, "scope-a")
    second = service.check(policy, "scope-a")
    denied = service.check(policy, "scope-a")

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert denied.allowed is False
    assert denied.retry_after_seconds == 10

    clock.advance(10)
    reset = service.check(policy, "scope-a")

    assert reset.allowed is True
    assert reset.remaining == 1


def test_fixed_window_concurrency_independent_keys_cleanup_and_disabled_behavior():
    clock = ManualClock()
    backend = InMemoryFixedWindowRateLimitBackend(clock=clock, max_buckets=3)
    service = RateLimitService(backend)
    policy = RateLimitPolicy(RateLimitPolicyId.INTEGRATION_INGEST, limit=5, window_seconds=5)

    with ThreadPoolExecutor(max_workers=20) as executor:
        decisions = list(executor.map(lambda _: service.check(policy, "shared"), range(25)))

    assert sum(decision.allowed for decision in decisions) == 5
    assert service.check(policy, "other").allowed is True
    assert backend.bucket_count() == 2

    clock.advance(5)
    assert service.check(policy, "fresh").allowed is True
    assert backend.cleanup_expired() == 0
    assert backend.bucket_count() == 1

    for index in range(10):
        assert service.check(policy, f"bounded-{index}").allowed is True
    assert backend.bucket_count() <= 3

    disabled = RateLimitService(backend, enabled=False)
    disabled_policy = RateLimitPolicy(RateLimitPolicyId.AUTH_LOGIN, limit=1, window_seconds=60)
    assert all(disabled.check(disabled_policy, "disabled-scope").allowed for _ in range(5))


def test_rate_limit_state_is_isolated_per_application_instance():
    policy = RateLimitPolicy(RateLimitPolicyId.AUTH_LOGIN, limit=1, window_seconds=60)

    def build_app() -> FastAPI:
        local_app = FastAPI()
        local_app.state.rate_limit_backend = InMemoryFixedWindowRateLimitBackend()

        def guard(request: Request) -> None:
            try:
                RateLimitService(request.app.state.rate_limit_backend).enforce(
                    policy,
                    "same-trusted-scope",
                )
            except RateLimitExceeded as exc:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers=rate_limit_headers(exc.decision),
                ) from exc

        @local_app.get("/limited")
        def limited(_: None = Depends(guard)) -> dict[str, bool]:
            return {"ok": True}

        return local_app

    with TestClient(build_app()) as first_client, TestClient(build_app()) as second_client:
        assert first_client.get("/limited").status_code == 200
        assert first_client.get("/limited").status_code == 429
        assert second_client.get("/limited").status_code == 200


def test_login_route_limits_successful_and_failed_attempts_without_credential_leak(client):
    _install_rate_limit_settings(rate_limit_auth_login_limit=2)

    failed = client.post(
        "/api/auth/login",
        json={"email": "fixture-operator@example.com", "password": "wrong-password"},
    )
    successful = client.post(
        "/api/auth/login",
        json={"email": "fixture-operator@example.com", "password": "fixture-password"},
    )
    limited = client.post(
        "/api/auth/login",
        headers={REQUEST_ID_HEADER: "task293-login-limit"},
        json={"email": "attacker@example.test", "password": "fixture-password"},
    )

    assert failed.status_code == 401
    assert successful.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]
    assert limited.headers[REQUEST_ID_HEADER] == "task293-login-limit"
    serialized = limited.text
    for forbidden in (
        "fixture-operator@example.com",
        "attacker@example.test",
        "fixture-password",
        "wrong-password",
        "testclient",
    ):
        assert forbidden not in serialized


def test_login_rate_limit_uses_source_not_email_or_password_and_sources_are_isolated(client):
    _install_rate_limit_settings(rate_limit_auth_login_limit=1)

    first = client.post(
        "/api/auth/login",
        json={"email": "fixture-operator@example.com", "password": "bad-one"},
    )
    same_source = client.post(
        "/api/auth/login",
        json={"email": "different@example.test", "password": "different-secret"},
    )

    with TestClient(app, client=("source-b.test", 50000)) as second_source:
        isolated = second_source.post(
            "/api/auth/login",
            json={"email": "fixture-operator@example.com", "password": "bad-two"},
        )

    assert first.status_code == 401
    assert same_source.status_code == 429
    assert isolated.status_code == 401


def test_integration_ingest_rate_limit_is_verified_account_scoped_and_has_no_mutation(
    client,
    signed_webhook_request,
):
    _install_rate_limit_settings(rate_limit_integration_ingest_limit=1)
    workspace_a = _create_workspace(client, "task293-ingest-a")
    workspace_b = _create_workspace(client, "task293-ingest-b")
    workspace_c = _create_workspace(client, "task293-ingest-c")
    account_a = _provision_account(client, "task293-ingest-a", "unused")
    account_b = _provision_account(client, "task293-ingest-b", "unused")
    account_c = _provision_account(client, "task293-ingest-c", "unused")
    payload = {
        "lead_id": str(uuid4()),
        "channel": "website_chat",
        "content": "Task 293 text",
        "external_event_id": "task293-event-a",
        "workspace_id": str(workspace_b["id"]),
    }

    first = _post_signed(
        client,
        signed_webhook_request,
        account_a["credential"],
        "/api/integrations/inbound-events",
        {key: value for key, value in payload.items() if key != "workspace_id"},
    )
    receipts_before_429 = len(_rows(InboundIntegrationEventReceipt))
    limited = _post_signed(
        client,
        signed_webhook_request,
        account_a["credential"],
        "/api/integrations/inbound-events",
        {key: value for key, value in payload.items() if key != "workspace_id"},
    )
    account_b_first = _post_signed(
        client,
        signed_webhook_request,
        account_b["credential"],
        "/api/integrations/inbound-events",
        {
            "lead_id": str(uuid4()),
            "channel": "website_chat",
            "content": "Task 293 text",
            "external_event_id": "task293-event-b",
        },
    )
    invalid_body_attempt = _post_signed(
        client,
        signed_webhook_request,
        account_c["credential"],
        "/api/integrations/inbound-events",
        {
            "lead_id": str(uuid4()),
            "channel": "website_chat",
            "content": "Task 293 text",
            "external_event_id": "task293-event-c",
            "workspace_id": workspace_a["id"],
        },
    )
    invalid_auth = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": "unknown-task293"},
        json={
            "lead_id": str(uuid4()),
            "channel": "website_chat",
            "content": "Task 293 text",
        },
    )

    assert first.status_code == 404
    assert limited.status_code == 429
    assert account_b_first.status_code == 404
    assert invalid_body_attempt.status_code == 422
    assert invalid_auth.status_code == 401
    assert len(_rows(InboundIntegrationEventReceipt)) == receipts_before_429
    assert len(_rows(ConversationMessage)) == 0
    assert workspace_a["id"] not in limited.text
    assert workspace_b["id"] not in limited.text
    assert workspace_c["id"] not in invalid_body_attempt.text


def test_provider_status_ingest_is_limited_after_verification_and_before_persistence(
    client,
    signed_webhook_request,
):
    _install_rate_limit_settings(rate_limit_integration_ingest_limit=1)
    _create_workspace(client, "task293-status")
    account = _provision_account(client, "task293-status", "unused")
    payload = {
        "provider_delivery_id": "task293-provider-delivery",
        "provider_status": "sent",
        "provider_timestamp": "2026-08-11T12:00:00Z",
    }

    first = _post_signed(
        client,
        signed_webhook_request,
        account["credential"],
        "/api/integrations/inbound-events/provider-status-events",
        payload,
    )
    before_limited = len(_rows(OutboundProviderDeliveryStatusEvent))
    limited = _post_signed(
        client,
        signed_webhook_request,
        account["credential"],
        "/api/integrations/inbound-events/provider-status-events",
        payload,
    )

    assert first.status_code == 404
    assert limited.status_code == 429
    assert len(_rows(OutboundProviderDeliveryStatusEvent)) == before_limited
    assert "task293-provider-delivery" not in limited.text


def test_outbound_delivery_limit_blocks_before_attempts_or_action_mutation(client):
    _install_rate_limit_settings(rate_limit_outbound_delivery_limit=1)
    _create_workspace(client, "task293-outbound")
    account = _provision_account(client, "task293-outbound")
    action = _create_outbound_action(
        client,
        "task293-outbound",
        account["id"],
        requires_approval=True,
    )
    url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver"

    approval_block = client.post(url, headers=_headers("task293-outbound"))
    before_limited_attempts = len(_rows(OutboundIntegrationDeliveryAttempt))
    before_limited_action = _rows(OutboundIntegrationAction)[0]
    limited = client.post(url, headers=_headers("task293-outbound"))
    after_limited_action = _rows(OutboundIntegrationAction)[0]

    assert approval_block.status_code == 409
    assert limited.status_code == 429
    assert len(_rows(OutboundIntegrationDeliveryAttempt)) == before_limited_attempts == 0
    assert before_limited_action.status == OutboundIntegrationActionStatus.PENDING
    assert after_limited_action.status == OutboundIntegrationActionStatus.PENDING
    assert after_limited_action.provider_delivery_id is None


def test_outbound_retry_route_is_limited_without_second_attempt(client):
    _install_rate_limit_settings(rate_limit_outbound_delivery_limit=1)
    _create_workspace(client, "task293-retry")
    account = _provision_account(client, "task293-retry")
    action = _create_outbound_action(client, "task293-retry", account["id"])

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        persisted = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert persisted is not None
        persisted.status = OutboundIntegrationActionStatus.FAILED
        persisted.failed_at = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        persisted.failure_code = "temporary_failure"
        persisted.failure_message = "Temporary failure"
        session.add(persisted)
        session.commit()

    url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/retry"
    retried = client.post(url, headers=_headers("task293-retry"))
    attempts_after_retry = len(_rows(OutboundIntegrationDeliveryAttempt))
    limited = client.post(url, headers=_headers("task293-retry"))

    assert retried.status_code == 200
    assert limited.status_code == 429
    assert len(_rows(OutboundIntegrationDeliveryAttempt)) == attempts_after_retry
    assert _rows(OutboundIntegrationAction)[0].status == OutboundIntegrationActionStatus.DELIVERED


def test_ai_conversation_limit_is_workspace_scoped_and_blocks_before_sales_processing(
    client,
    monkeypatch,
):
    _install_rate_limit_settings(rate_limit_ai_conversation_limit=1)
    _create_workspace(client, "task293-ai-a")
    _create_workspace(client, "task293-ai-b")
    lead_a = _create_lead(client, "task293-ai-a")
    lead_b = _create_lead(client, "task293-ai-b")

    first = client.post(
        f"/api/conversations/{lead_a['id']}/reply",
        headers=_headers("task293-ai-a"),
        json={"channel": "console", "content": "Task 293 text"},
    )
    first_b = client.post(
        f"/api/conversations/{lead_b['id']}/reply",
        headers=_headers("task293-ai-b"),
        json={"channel": "console", "content": "Task 293 text"},
    )
    messages_before_limited = len(_rows(ConversationMessage))
    usage_before_limited = len(_rows(AIInvocationUsage))

    async def fail_if_sales_turn_runs(*args, **kwargs):
        raise AssertionError("rate-limited conversation must not reach Sales processing")

    monkeypatch.setattr(
        "app.api.routes.conversations.DirectSalesConversationTurnService.process",
        fail_if_sales_turn_runs,
    )
    limited = client.post(
        f"/api/conversations/{lead_a['id']}/reply",
        headers=_headers("task293-ai-a"),
        json={"channel": "console", "content": "Task 293 text"},
    )

    assert first.status_code == 200
    assert first_b.status_code == 200
    assert limited.status_code == 429
    assert len(_rows(ConversationMessage)) == messages_before_limited
    assert len(_rows(AIInvocationUsage)) == usage_before_limited


def test_disabled_rate_limiting_preserves_existing_behavior(client):
    _install_rate_limit_settings(
        rate_limit_enabled=False,
        rate_limit_auth_login_limit=1,
    )

    first = client.post(
        "/api/auth/login",
        json={"email": "fixture-operator@example.com", "password": "fixture-password"},
    )
    second = client.post(
        "/api/auth/login",
        json={"email": "fixture-operator@example.com", "password": "fixture-password"},
    )

    assert first.status_code == 200
    assert second.status_code == 200


def test_rate_limited_requests_keep_task291_logs_and_task292_metrics_safe(client, caplog):
    _install_rate_limit_settings(rate_limit_auth_login_limit=1)
    caplog.set_level(logging.INFO, logger="app.http")
    before_metrics = client.get("/metrics").text
    before_429 = _sample_value(
        before_metrics,
        "http_requests_total",
        {"method": "POST", "route": "/api/auth/login", "status_code": "429"},
    )
    assert client.post(
        "/api/auth/login",
        json={"email": "fixture-operator@example.com", "password": "fixture-password"},
    ).status_code == 200

    limited = client.post(
        "/api/auth/login",
        headers={REQUEST_ID_HEADER: "task293-observability"},
        json={"email": "fixture-operator@example.com", "password": "fixture-password"},
    )
    after_metrics = client.get("/metrics").text
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http_request_completed"
        and getattr(record, "status_code", None) == 429
    ]
    serialized_records = "\n".join(
        json.dumps(record.structured_fields, sort_keys=True) for record in records
    )

    assert limited.status_code == 429
    assert limited.headers[REQUEST_ID_HEADER] == "task293-observability"
    assert len(records) == 1
    assert records[0].route == "/api/auth/login"
    assert _sample_value(
        after_metrics,
        "http_requests_total",
        {"method": "POST", "route": "/api/auth/login", "status_code": "429"},
    ) == before_429 + 1
    for forbidden in (
        "fixture-operator@example.com",
        "fixture-password",
        "testclient",
        "client_source",
        "integration_account",
        "outbound_delivery",
        "ai_conversation",
    ):
        assert forbidden not in limited.text
        assert forbidden not in serialized_records
        assert forbidden not in after_metrics
