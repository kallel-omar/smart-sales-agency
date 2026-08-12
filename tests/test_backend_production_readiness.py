import hmac
import json
import logging
import os
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, select

from alembic import command
from app import db
from app.api.dependencies import get_settings
from app.config import Settings
from app.config import get_settings as cached_settings
from app.db import get_session
from app.main import create_app
from app.migration_state import (
    MigrationSchemaNotCurrentError,
    MigrationSchemaState,
    application_head_revision,
    check_database_schema_state,
)
from app.models import (
    AIInvocationStatus,
    AIInvocationUsage,
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    InboundIntegrationEventReceipt,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationAuditAction,
    OutboundIntegrationAuditEvent,
    OutboundIntegrationDeliveryAttempt,
    OutboundProviderDeliveryStatusEvent,
    ProviderDeliveryStatus,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.observability import REQUEST_ID_HEADER
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.ai_invocation_usage import AIInvocationUsageService
from app.services.ai_model_tiers import AIModelTier, AIModelTierResolver
from app.services.delivery_adapters import WebhookHttpResponse
from app.services.llm import LLMClient, LLMCompletion
from app.services.workspace_ai_usage_limits import (
    AIWorkspaceUsageLimitOutcome,
    AIWorkspaceUsageLimitPolicy,
    AIWorkspaceUsageLimitReasonCode,
    AIWorkspaceUsageLimitRequest,
)

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"
ALEMBIC_DIR = ROOT / "alembic"
READINESS_DOC = ROOT / "docs" / "backend-production-readiness.md"
PRODUCTION_RUNTIME_DOC = ROOT / "docs" / "production-runtime.md"
DATABASE_RECOVERY_DOC = ROOT / "docs" / "database-recovery.md"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"

SAFE_PRODUCTION_SECRET = "task300-safe-production-secret-value"
TEST_PASSWORD = "correct-password"
INTEGRATION_SECRET_REFERENCE = "INTEGRATION_SECRET_TASK300"
INTEGRATION_SECRET_VALUE = "task300-integration-secret-value"


class InspectingFakeLLM(LLMClient):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return (await self.complete_with_metadata(system_prompt, user_prompt)).content

    async def complete_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMCompletion:
        self.calls.append((system_prompt, user_prompt))
        return LLMCompletion(
            content=(
                "Salem Sarra, Task 300 Starter soumou 99.00 TND/month. "
                "T7eb nfassarlek chnowa fih?"
            ),
            input_tokens=40,
            output_tokens=20,
        )


def _alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return config


@pytest.fixture
def postgres_schema_url(monkeypatch) -> Generator[str, None, None]:
    database_url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required for Task 300 PostgreSQL E2E")

    base_url = make_url(database_url)
    schema_name = f"task300_{uuid4().hex}"
    admin_engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    test_url = base_url.set(
        query={**base_url.query, "options": f"-csearch_path={schema_name}"}
    ).render_as_string(hide_password=False)

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    try:
        yield test_url
    finally:
        cached_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
        monkeypatch.delenv(INTEGRATION_SECRET_REFERENCE, raising=False)


def _settings(database_url: str, **overrides) -> Settings:
    values = {
        "environment": "production",
        "database_url": database_url,
        "auth_token_secret": SAFE_PRODUCTION_SECRET,
        "api_docs_enabled": None,
        "metrics_enabled": True,
        "llm_mode": "openai_compatible",
        "llm_api_key": "task300-test-only-key",
        "require_human_approval": True,
        "outbound_webhook_url": "https://transport.example.test/whatsapp-cloud",
        "outbound_webhook_signing_enabled": True,
        "database_startup_max_attempts": 2,
        "database_startup_retry_delay_seconds": 0,
        "rate_limit_auth_login_limit": 1000,
        "rate_limit_integration_ingest_limit": 1000,
        "rate_limit_outbound_delivery_limit": 1000,
        "rate_limit_ai_conversation_limit": 1,
        "ai_model_tier_mappings": {
            "economy": {"provider": "test-provider", "model": "economy-model"},
            "standard": {"provider": "test-provider", "model": "standard-model"},
        },
        "ai_model_pricing": [
            {
                "provider": "test-provider",
                "model": "standard-model",
                "input_cost_per_million_tokens": "2.00",
                "output_cost_per_million_tokens": "4.00",
            }
        ],
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _upgrade_head(monkeypatch, database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    cached_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(), "head")
    finally:
        cached_settings.cache_clear()


def _session_dependency(engine):
    def get_test_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    return get_test_session


def _signed_request(
    integration_key: str,
    payload: dict,
    *,
    event_id: str | None = None,
) -> tuple[dict[str, str], bytes]:
    timestamp = str(int(time.time()))
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        INTEGRATION_SECRET_VALUE.encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Integration-Key": integration_key,
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Signature": signature,
    }
    if event_id is not None:
        headers["X-Webhook-Event-Id"] = event_id
        headers["X-Integration-Event-Id"] = event_id
    return headers, body


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workspace_headers(slug: str, token: str) -> dict[str, str]:
    return {**_auth_headers(token), "X-Workspace-Slug": slug}


def _register_and_login(client: TestClient, email: str) -> tuple[UUID, str]:
    registered = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": TEST_PASSWORD,
            "display_name": "Task 300 Operator",
        },
    )
    assert registered.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    return UUID(registered.json()["id"]), login.json()["access_token"]


def _metric_value(metrics_text: str, sample_name: str, labels: dict[str, str]) -> float:
    for family in text_string_to_metric_families(metrics_text):
        for sample in family.samples:
            if sample.name == sample_name and dict(sample.labels) == labels:
                return float(sample.value)
    return 0.0


class _ListLogHandler(logging.Handler):
    def __init__(self, records: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.INFO)
        self.records = records

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _completion_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [
        record
        for record in records
        if getattr(record, "event", None) == "http_request_completed"
    ]


def _serialized_logs(records: list[logging.LogRecord]) -> str:
    return "\n".join(
        json.dumps(record.structured_fields, sort_keys=True)
        for record in records
        if hasattr(record, "structured_fields")
    )


def _count(session: Session, model) -> int:
    return len(session.exec(select(model)).all())


def _assert_doc_contains(document: str, snippets: tuple[str, ...]) -> None:
    for snippet in snippets:
        assert snippet in document


def test_postgresql_production_backend_readiness_e2e(
    postgres_schema_url,
    monkeypatch,
):
    settings = _settings(postgres_schema_url)
    monkeypatch.setenv(INTEGRATION_SECRET_REFERENCE, INTEGRATION_SECRET_VALUE)

    empty_check = check_database_schema_state(postgres_schema_url)
    assert empty_check.state is MigrationSchemaState.UNINITIALIZED
    with pytest.raises(MigrationSchemaNotCurrentError), TestClient(create_app(settings)):
        pass

    _upgrade_head(monkeypatch, postgres_schema_url)
    current_check = check_database_schema_state(postgres_schema_url)
    assert current_check.state is MigrationSchemaState.CURRENT
    assert current_check.current_revisions == (application_head_revision(),)

    engine = db.create_app_engine(postgres_schema_url)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = _session_dependency(engine)
    log_records: list[logging.LogRecord] = []
    log_handler = _ListLogHandler(log_records)
    observed_loggers = (logging.getLogger("app.http"), logging.getLogger("app.errors"))
    for logger in observed_loggers:
        logger.addHandler(log_handler)
        logger.setLevel(logging.INFO)

    fake_llm = InspectingFakeLLM()
    built_models: list[str] = []

    def build_gateway(session: Session, received_settings: Settings) -> AIInvocationGateway:
        def build_client(_settings: Settings, *, model: str) -> LLMClient:
            built_models.append(model)
            return fake_llm

        return AIInvocationGateway(session, received_settings, llm_builder=build_client)

    monkeypatch.setattr("app.services.inbound_integrations.AIInvocationGateway", build_gateway)
    monkeypatch.setattr(
        "app.departments.sales.services.conversation_turn_service.AIInvocationGateway",
        build_gateway,
    )

    delivery_calls: list[dict] = []

    def fake_post(self, url, *, content: bytes, headers: dict[str, str], timeout):
        del self, timeout
        body = json.loads(content)
        delivery_calls.append({"url": url, "headers": headers, "body": body})
        assert url == settings.outbound_webhook_url
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Webhook-Signing"] == "hmac-sha256"
        assert headers["X-Webhook-Timestamp"]
        assert headers["X-Webhook-Signature"]
        assert body["provider"] == "whatsapp_cloud"
        assert body["action_type"] == "send_message"
        assert body["content"] == "Task 300 approved outbound reply"
        assert body["external_account_id"] == "task300-phone-number-id"
        assert "token" not in json.dumps(body).lower()
        return WebhookHttpResponse(
            status_code=202,
            headers={"x-delivery-id": "task300-provider-delivery-id"},
        )

    monkeypatch.setattr("app.services.delivery_adapters.HttpxWebhookHttpTransport.post", fake_post)

    @app.get("/task300/synthetic-error")
    def synthetic_error():
        raise RuntimeError(
            "password=synthetic-secret Authorization=Bearer synthetic-token "
            "postgresql://user:synthetic-db-secret@example.test/db "
            "provider_api_key=synthetic-provider-secret"
        )

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            health = client.get("/health", headers={REQUEST_ID_HEADER: "task300-health"})
            docs = client.get("/docs")
            openapi = client.get("/openapi.json")

            assert health.status_code == 200
            assert docs.status_code == 404
            assert openapi.status_code == 404
            assert health.headers[REQUEST_ID_HEADER] == "task300-health"

            owner_id, owner_token = _register_and_login(
                client,
                "task300-owner@example.test",
            )
            member_id, member_token = _register_and_login(
                client,
                "task300-member@example.test",
            )
            workspace_a = client.post(
                "/api/workspaces",
                headers=_auth_headers(owner_token),
                json={"slug": "task300-a", "name": "Task 300 A"},
            )
            workspace_b = client.post(
                "/api/workspaces",
                headers=_auth_headers(owner_token),
                json={"slug": "task300-b", "name": "Task 300 B"},
            )
            assert workspace_a.status_code == 201
            assert workspace_b.status_code == 201
            workspace_a_id = UUID(workspace_a.json()["id"])
            workspace_b_id = UUID(workspace_b.json()["id"])

            with Session(engine) as session:
                member = WorkspaceMember(
                    workspace_id=workspace_a_id,
                    user_id=member_id,
                    role=WorkspaceMemberRole.MEMBER,
                )
                session.add(member)
                workspace = session.get(Workspace, workspace_a_id)
                assert workspace is not None
                workspace.sales_preferred_language = "tunisian_arabic"
                workspace.sales_preferred_script = "latin"
                session.add(workspace)
                session.commit()

            member_allowed = client.post(
                "/api/leads",
                headers=_workspace_headers("task300-a", member_token),
                json={
                    "tenant_id": "body-cannot-own-tenancy",
                    "full_name": "Member Lead",
                    "company_name": "Member Company",
                    "source": "manual",
                },
            )
            member_denied = client.post(
                "/api/integrations/accounts",
                headers=_workspace_headers("task300-a", member_token),
                json={
                    "provider": "whatsapp_cloud",
                    "external_account_id": "member-denied",
                    "secret_reference": INTEGRATION_SECRET_REFERENCE,
                },
            )
            assert member_allowed.status_code == 201
            assert member_allowed.json()["tenant_id"] == "task300-a"
            assert member_denied.status_code == 403

            product = client.post(
                "/api/products",
                headers=_workspace_headers("task300-a", owner_token),
                json={
                    "tenant_id": "ignored",
                    "name": "Task 300 Starter",
                    "description": "WhatsApp sales automation for small teams.",
                    "price": 99,
                    "minimum_price": 89,
                    "metadata_json": {"billing": "TND/month"},
                },
            )
            lead = client.post(
                "/api/leads",
                headers=_workspace_headers("task300-a", owner_token),
                json={
                    "tenant_id": "task300-b",
                    "full_name": "Sarra Ben Ali",
                    "company_name": "Example Commerce",
                    "email": "sarra.task300@example.test",
                    "phone": "task300-recipient",
                    "source": "whatsapp_cloud",
                },
            )
            account_a = client.post(
                "/api/integrations/accounts",
                headers=_workspace_headers("task300-a", owner_token),
                json={
                    "provider": "whatsapp_cloud",
                    "external_account_id": "task300-phone-number-id",
                    "secret_reference": INTEGRATION_SECRET_REFERENCE,
                },
            )
            account_b = client.post(
                "/api/integrations/accounts",
                headers=_workspace_headers("task300-b", owner_token),
                json={
                    "provider": "whatsapp_cloud",
                    "external_account_id": "task300-other-phone-number-id",
                    "secret_reference": INTEGRATION_SECRET_REFERENCE,
                },
            )
            assert product.status_code == 201
            assert lead.status_code == 201
            assert lead.json()["tenant_id"] == "task300-a"
            assert account_a.status_code == 201
            assert account_b.status_code == 201
            account_a_body = account_a.json()
            account_b_body = account_b.json()

            readiness = client.get(
                f"/api/integrations/accounts/{account_a_body['id']}/health/runtime-readiness",
                headers=_workspace_headers("task300-a", owner_token),
            )
            assert readiness.status_code == 200
            assert readiness.json()["configuration_ready"] is True

            inbound_payload = {
                "lead_id": lead.json()["id"],
                "channel": "whatsapp_cloud",
                "content": "salam, 9adech prix el produit hedha?",
                "external_event_id": "task300-provider-body-event",
            }
            inbound_headers, inbound_body = _signed_request(
                account_a_body["inbound_credential"],
                inbound_payload,
                event_id="task300-inbound-event-1",
            )
            inbound = client.post(
                "/api/integrations/inbound-events",
                headers={**inbound_headers, REQUEST_ID_HEADER: "task300-inbound"},
                content=inbound_body,
            )
            duplicate_inbound = client.post(
                "/api/integrations/inbound-events",
                headers=inbound_headers,
                content=inbound_body,
            )
            assert inbound.status_code == 200
            inbound_body_json = inbound.json()
            assert duplicate_inbound.status_code == 200
            assert duplicate_inbound.json() == {
                "duplicate": True,
                "correlation_id": inbound_body_json["correlation_id"],
            }
            assert inbound_body_json["approval_id"] is not None
            assert inbound_body_json["draft_reply"].startswith("Salem Sarra")
            assert "Task 300 Starter" in inbound_body_json["draft_reply"]
            assert "99.00" in inbound_body_json["draft_reply"]
            assert "50.00" not in inbound_body_json["draft_reply"]
            assert built_models == ["standard-model"]
            assert len(fake_llm.calls) == 1
            system_prompt, user_prompt = fake_llm.calls[0]
            rendered_prompt = system_prompt + "\n" + user_prompt
            assert "Task 300 Starter" in rendered_prompt
            assert "Price: 99.00" in rendered_prompt
            assert "50.00" not in rendered_prompt
            assert "salam, 9adech prix el produit hedha?" in user_prompt

            history = client.get(
                f"/api/conversations/{lead.json()['id']}",
                headers=_workspace_headers("task300-a", owner_token),
            )
            cross_history = client.get(
                f"/api/conversations/{lead.json()['id']}",
                headers=_workspace_headers("task300-b", owner_token),
            )
            assert history.status_code == 200
            assert [message["direction"] for message in history.json()] == ["inbound"]
            assert history.json()[0]["channel"] == "whatsapp_cloud"
            assert cross_history.status_code == 404

            approved_inbound = client.post(
                f"/api/approvals/{inbound_body_json['approval_id']}/approve",
                headers=_workspace_headers("task300-a", owner_token),
                json={"reviewer_note": "Task 300 approved"},
            )
            cross_approval = client.post(
                f"/api/approvals/{inbound_body_json['approval_id']}/approve",
                headers=_workspace_headers("task300-b", owner_token),
                json={"reviewer_note": "cross workspace"},
            )
            assert approved_inbound.status_code == 200
            assert approved_inbound.json()["status"] == ApprovalStatus.EXECUTED.value
            assert cross_approval.status_code == 404

            action = client.post(
                f"/api/integrations/accounts/{account_a_body['id']}/outbound-actions",
                headers=_workspace_headers("task300-a", owner_token),
                json={
                    "external_target_id": "task300-recipient",
                    "action_type": "send_message",
                    "content": "Task 300 approved outbound reply",
                    "payload": {},
                    "correlation_id": inbound_body_json["correlation_id"],
                    "idempotency_key": "task300-outbound-action-1",
                    "requires_approval": True,
                },
            )
            cross_action = client.get(
                f"/api/integrations/outbound-actions/{action.json()['id']}",
                headers=_workspace_headers("task300-b", owner_token),
            )
            cross_account_action = client.post(
                f"/api/integrations/accounts/{account_b_body['id']}/outbound-actions",
                headers=_workspace_headers("task300-a", owner_token),
                json={
                    "external_target_id": "task300-recipient",
                    "action_type": "send_message",
                    "content": "must not persist",
                    "idempotency_key": "task300-cross-workspace",
                },
            )
            assert action.status_code == 201
            assert action.json()["requires_approval"] is True
            assert action.json()["approval_request_id"] is not None
            assert cross_action.status_code == 404
            assert cross_account_action.status_code == 404

            deliver_before_approval = client.post(
                (
                    f"/api/integrations/accounts/{account_a_body['id']}/outbound-actions/"
                    f"{action.json()['id']}/deliver"
                ),
                headers=_workspace_headers("task300-a", owner_token),
            )
            assert deliver_before_approval.status_code == 409
            assert delivery_calls == []

            approved_action = client.post(
                f"/api/approvals/{action.json()['approval_request_id']}/approve",
                headers=_workspace_headers("task300-a", owner_token),
                json={"reviewer_note": "Approve outbound"},
            )
            delivered = client.post(
                (
                    f"/api/integrations/accounts/{account_a_body['id']}/outbound-actions/"
                    f"{action.json()['id']}/deliver"
                ),
                headers=_workspace_headers("task300-a", owner_token),
            )
            assert approved_action.status_code == 200
            assert approved_action.json()["status"] == ApprovalStatus.APPROVED.value
            assert delivered.status_code == 200
            assert delivered.json()["status"] == OutboundIntegrationActionStatus.DELIVERED.value
            assert delivered.json()["provider_delivery_id"] == "task300-provider-delivery-id"
            assert len(delivery_calls) == 1

            delivered_at = datetime.now(UTC)
            status_payloads = [
                {
                    "provider_delivery_id": delivered.json()["provider_delivery_id"],
                    "provider_status": ProviderDeliveryStatus.DELIVERED.value,
                    "provider_timestamp": (delivered_at + timedelta(seconds=1)).isoformat(),
                },
                {
                    "provider_delivery_id": delivered.json()["provider_delivery_id"],
                    "provider_status": ProviderDeliveryStatus.SENT.value,
                    "provider_timestamp": delivered_at.isoformat(),
                },
                {
                    "provider_delivery_id": delivered.json()["provider_delivery_id"],
                    "provider_status": ProviderDeliveryStatus.READ.value,
                    "provider_timestamp": (delivered_at + timedelta(seconds=2)).isoformat(),
                },
            ]
            callback_results = []
            for payload in status_payloads:
                headers, body = _signed_request(account_a_body["inbound_credential"], payload)
                callback_results.append(
                    client.post(
                        "/api/integrations/inbound-events/provider-status-events",
                        headers=headers,
                        content=body,
                    )
                )
            duplicate_headers, duplicate_body = _signed_request(
                account_a_body["inbound_credential"],
                status_payloads[0],
            )
            duplicate_status = client.post(
                "/api/integrations/inbound-events/provider-status-events",
                headers=duplicate_headers,
                content=duplicate_body,
            )
            for result in callback_results:
                assert result.status_code == 200
                assert result.json()["duplicate"] is False
            assert duplicate_status.status_code == 200
            assert duplicate_status.json()["duplicate"] is True

            status_history = client.get(
                (
                    f"/api/integrations/accounts/{account_a_body['id']}/outbound-actions/"
                    f"{action.json()['id']}/provider-status-events"
                ),
                headers=_workspace_headers("task300-a", owner_token),
            )
            cross_status_callback_headers, cross_status_callback_body = _signed_request(
                account_b_body["inbound_credential"],
                status_payloads[1],
            )
            cross_status_callback = client.post(
                "/api/integrations/inbound-events/provider-status-events",
                headers=cross_status_callback_headers,
                content=cross_status_callback_body,
            )
            assert status_history.status_code == 200
            assert [event["provider_status"] for event in status_history.json()] == [
                "sent",
                "delivered",
                "read",
            ]
            assert cross_status_callback.status_code == 404

            ai_usage = client.get(
                "/api/integrations/ai-usage",
                headers=_workspace_headers("task300-a", owner_token),
            )
            ai_usage_summary = client.get(
                "/api/integrations/ai-usage/summary",
                headers=_workspace_headers("task300-a", owner_token),
            )
            assert ai_usage.status_code == 200
            assert len(ai_usage.json()) == 1
            usage = ai_usage.json()[0]
            assert usage["task_identifier"] == "sales.conversation.reply"
            assert usage["agent_identifier"] == "sales_conversation"
            assert usage["provider"] == "test-provider"
            assert usage["model"] == "standard-model"
            assert usage["status"] == AIInvocationStatus.SUCCESSFUL.value
            assert usage["input_tokens"] == 40
            assert usage["output_tokens"] == 20
            assert usage["total_tokens"] == 60
            assert Decimal(usage["estimated_cost"]) == Decimal("0.000160")
            assert ai_usage_summary.status_code == 200
            assert ai_usage_summary.json()["invocation_count"] == 1

            with Session(engine) as session:
                workspace = session.get(Workspace, workspace_a_id)
                assert workspace is not None
                workspace.ai_invocation_limit = 1
                session.add(workspace)
                session.commit()
                decision = AIWorkspaceUsageLimitPolicy(
                    AIInvocationUsageService(session),
                    AIModelTierResolver.from_settings(settings),
                ).evaluate(AIWorkspaceUsageLimitRequest(workspace, AIModelTier.STANDARD))
                assert decision.outcome is AIWorkspaceUsageLimitOutcome.BLOCKED
                assert (
                    decision.reason_code
                    is AIWorkspaceUsageLimitReasonCode.INVOCATION_LIMIT_REACHED
                )
                workspace.ai_invocation_limit = None
                session.add(workspace)
                session.commit()

            first_direct = client.post(
                f"/api/conversations/{lead.json()['id']}/reply",
                headers={
                    **_workspace_headers("task300-a", owner_token),
                    REQUEST_ID_HEADER: "task300-direct",
                },
                json={
                    "channel": "whatsapp_cloud",
                    "content": "salam, 9adech prix el produit hedha?",
                },
            )
            assert first_direct.status_code == 200
            with Session(engine) as session:
                direct_before_messages = _count(session, ConversationMessage)
                direct_before_usage = _count(session, AIInvocationUsage)
            rate_limited = client.post(
                f"/api/conversations/{lead.json()['id']}/reply",
                headers={
                    **_workspace_headers("task300-a", owner_token),
                    REQUEST_ID_HEADER: "task300-rate-limited",
                },
                json={
                    "channel": "whatsapp_cloud",
                    "content": "salam, 9adech prix el produit hedha?",
                },
            )
            with Session(engine) as session:
                assert _count(session, ConversationMessage) == direct_before_messages
                assert _count(session, AIInvocationUsage) == direct_before_usage
            assert rate_limited.status_code == 429
            assert rate_limited.headers["Retry-After"]
            assert rate_limited.headers["X-RateLimit-Limit"] == "1"
            assert rate_limited.headers[REQUEST_ID_HEADER] == "task300-rate-limited"

            safe_error = client.get(
                "/task300/synthetic-error",
                headers={REQUEST_ID_HEADER: "task300-safe-error"},
            )
            assert safe_error.status_code == 500
            assert safe_error.headers[REQUEST_ID_HEADER] == "task300-safe-error"
            assert safe_error.json() == {
                "detail": "Internal server error",
                "request_id": "task300-safe-error",
            }
            forbidden_response_text = (
                "synthetic-secret",
                "synthetic-token",
                "synthetic-db-secret",
                "synthetic-provider-secret",
                "Traceback",
                "RuntimeError",
            )
            for forbidden in forbidden_response_text:
                assert forbidden not in safe_error.text

            validation = client.post(
                "/api/auth/register",
                json={
                    "email": "task300-validation@example.test",
                    "password": "short-secret-value",
                    "unexpected": "synthetic-password-value",
                },
            )
            assert validation.status_code == 422
            assert "synthetic-password-value" not in validation.text

            metrics = client.get("/metrics")
            assert metrics.status_code == 200
            metrics_text = metrics.text
            assert _metric_value(
                metrics_text,
                "http_requests_total",
                {
                    "method": "POST",
                    "route": "/api/integrations/inbound-events",
                    "status_code": "200",
                },
            ) >= 2
            assert _metric_value(
                metrics_text,
                "http_requests_total",
                {
                    "method": "POST",
                    "route": "/api/conversations/{lead_id}/reply",
                    "status_code": "429",
                },
            ) == 1
            assert _metric_value(
                metrics_text,
                "http_requests_total",
                {
                    "method": "GET",
                    "route": "/task300/synthetic-error",
                    "status_code": "500",
                },
            ) == 1
            for forbidden in (
                str(workspace_a_id),
                str(workspace_b_id),
                str(owner_id),
                account_a_body["id"],
                action.json()["id"],
                "task300-rate-limited",
                "task300-provider-delivery-id",
                "task300-recipient",
                "salam",
                "synthetic-secret",
            ):
                assert forbidden not in metrics_text

            logs = _serialized_logs(log_records)
            assert "task300-inbound" in logs
            assert "task300-rate-limited" in logs
            assert "task300-safe-error" in logs
            for forbidden in (
                TEST_PASSWORD,
                INTEGRATION_SECRET_VALUE,
                "task300-recipient",
                "task300-provider-delivery-id",
                "salam, 9adech",
                "synthetic-secret",
                "synthetic-token",
                "synthetic-db-secret",
                "synthetic-provider-secret",
            ):
                assert forbidden not in logs
            assert [
                record.structured_fields["route"]
                for record in _completion_records(log_records)
                if record.structured_fields["request_id"] == "task300-safe-error"
            ] == ["/task300/synthetic-error"]

            trace = client.get(
                f"/api/integrations/execution-traces/{inbound_body_json['correlation_id']}",
                headers=_workspace_headers("task300-a", owner_token),
            )
            assert trace.status_code == 200
            assert trace.json()["inbound"]["external_event_id"] == "task300-inbound-event-1"
            assert trace.json()["outbound_actions"][0]["id"] == action.json()["id"]

            with Session(engine) as session:
                assert _count(session, InboundIntegrationEventReceipt) == 1
                assert _count(session, AIInvocationUsage) == 2
                assert _count(session, OutboundProviderDeliveryStatusEvent) == 3
                assert _count(session, OutboundIntegrationDeliveryAttempt) == 1
                assert _count(session, OutboundIntegrationAction) == 1
                assert _count(session, ApprovalRequest) == 3
                assert _count(session, ConversationMessage) == 3
                stored_action = session.get(OutboundIntegrationAction, UUID(action.json()["id"]))
                assert stored_action is not None
                assert stored_action.status is OutboundIntegrationActionStatus.DELIVERED
                assert stored_action.provider_delivery_id == "task300-provider-delivery-id"
                attempt = session.exec(select(OutboundIntegrationDeliveryAttempt)).one()
                assert attempt.status is OutboundIntegrationActionStatus.DELIVERED
                audit_events = session.exec(
                    select(OutboundIntegrationAuditEvent).where(
                        OutboundIntegrationAuditEvent.outbound_integration_action_id
                        == stored_action.id
                    )
                ).all()
                assert [event.action for event in audit_events] == [
                    OutboundIntegrationAuditAction.CREATED,
                    OutboundIntegrationAuditAction.DELIVERY_ATTEMPTED,
                    OutboundIntegrationAuditAction.DELIVERED,
                ]
                assert session.get(User, owner_id) is not None
                assert inspect(engine).has_table("alembic_version")
    finally:
        for logger in observed_loggers:
            logger.removeHandler(log_handler)
        app.dependency_overrides.clear()
        engine.dispose()


def test_backend_readiness_matrix_and_manual_acceptance_runbooks_are_documented():
    document = READINESS_DOC.read_text(encoding="utf-8")

    _assert_doc_contains(
        document,
        (
            "# Backend Production Readiness",
            "## Readiness Matrix",
            "authentication | READY",
            "RBAC | READY",
            "workspace isolation | READY",
            "WhatsApp inbound boundary | READY",
            "WhatsApp outbound boundary | READY",
            "provider status callbacks | READY",
            "production Docker runtime | READY",
            "PostgreSQL | READY",
            "backup/restore | READY WITH DOCUMENTED LIMITATION",
            "Phase A manual acceptance | PASSED",
            "Phase B real WhatsApp acceptance | PASSED",
            "backend production readiness | PASSED",
            "frontend | NOT YET IMPLEMENTED",
            "billing/subscriptions | NOT YET IMPLEMENTED",
            "## Backend Go Criteria",
            "BACKEND READY FOR FRONTEND",
            "## Phase A - Local Production Backend E2E",
            "Status: PASSED.",
            "docker build -t smart-sales-agency:task300 .",
            "alembic upgrade head",
            "python -m app.migration_state check",
            "GET /health",
            "GET /docs",
            "GET /metrics",
            "## Phase B - Real WhatsApp E2E",
            "Do not print or paste secrets",
            "real client WhatsApp",
            "sent/delivered/read status history",
            "unsupported",
            "expired provider token",
        ),
    )
    for forbidden in (
        "task300-safe-production-secret-value",
        "task300-integration-secret-value",
        "postgresql+psycopg://task297:task297",
        "task300-provider-delivery-id",
        "task300-recipient",
        "sarra.task300@example.test",
    ):
        assert forbidden not in document


def test_task300_operational_artifacts_keep_existing_backend_contracts():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    production_doc = PRODUCTION_RUNTIME_DOC.read_text(encoding="utf-8")
    recovery_doc = DATABASE_RECOVERY_DOC.read_text(encoding="utf-8")

    assert "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\"" in dockerfile
    assert "--no-proxy-headers" in dockerfile
    assert "--reload" not in dockerfile
    assert "--workers" not in dockerfile
    assert "USER app" in dockerfile
    assert "127.0.0.1:8000/health" in dockerfile
    assert "alembic" in dockerfile
    assert "postgres:16" not in dockerfile

    for pattern in (
        ".env",
        "infra/n8n/.env",
        ".git",
        ".venv",
        "*.db",
        "*.backup",
        "*.dump",
        "backups/",
    ):
        assert pattern in dockerignore

    assert "POSTGRES_TEST_DATABASE_URL" in workflow
    assert "docker build -t smart-sales-agency:test ." in workflow
    assert "docker push" not in workflow
    assert "WHATSAPP_CLOUD_ACCESS_TOKEN" not in workflow

    _assert_doc_contains(
        production_doc + recovery_doc,
        (
            "Run one FastAPI process/worker for now.",
            "FastAPI never runs `alembic upgrade head` automatically.",
            "It does not retry business writes or mutate domain state",
            "`pg_dump --format=custom --no-owner --no-acl`",
            "`pg_restore --exit-on-error --single-transaction --no-owner --no-acl`",
        ),
    )

    combined = f"{dockerfile}\n{dockerignore}\n{workflow}\n{production_doc}\n{recovery_doc}"
    for forbidden in (
        "Bearer ",
        "task300-safe-production-secret-value",
        "task300-integration-secret-value",
        "provider_api_key",
    ):
        assert forbidden not in combined
