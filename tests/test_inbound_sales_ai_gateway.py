from decimal import Decimal
from uuid import UUID

import pytest
from sqlmodel import select

from app.api.dependencies import get_settings
from app.config import Settings
from app.db import get_session
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.agents.sales_agent import SalesConversationAgent
from app.main import app
from app.models import AIInvocationStatus, AIInvocationUsage, Lead, Workspace
from app.services.ai_invocation_gateway import AIInvocationBlockedError, AIInvocationGateway
from app.services.llm import LLMClient, LLMCompletion
from app.services.repository import SalesRepository


class FakeLLM(LLMClient):
    def __init__(self, *, content: str = "Gateway-generated sales reply") -> None:
        self.content = content
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return (await self.complete_with_metadata(system_prompt, user_prompt)).content

    async def complete_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMCompletion:
        self.calls.append((system_prompt, user_prompt))
        return LLMCompletion(content=self.content, input_tokens=10, output_tokens=5)


def _settings() -> Settings:
    return Settings(
        llm_mode="openai_compatible",
        llm_api_key="test-key",
        ai_model_tier_mappings={
            "economy": {"provider": "provider-a", "model": "economy-model"},
            "standard": {"provider": "provider-b", "model": "standard-model"},
        },
        ai_model_pricing=[
            {
                "provider": "provider-a",
                "model": "economy-model",
                "input_cost_per_million_tokens": "1.00",
                "output_cost_per_million_tokens": "3.00",
            },
            {
                "provider": "provider-b",
                "model": "standard-model",
                "input_cost_per_million_tokens": "2.00",
                "output_cost_per_million_tokens": "4.00",
            },
        ],
    )


def _provision_inbound_sales(client, integration_account_factory, slug: str = "inbound-gateway"):
    workspace_response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.title()},
    )
    assert workspace_response.status_code == 201
    workspace_id = UUID(workspace_response.json()["id"])
    credential = f"{slug}-key"
    integration_account_factory(workspace_id, credential)
    lead_response = client.post(
        "/api/leads",
        json={
            "tenant_id": slug,
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
            "source": "website",
        },
    )
    assert lead_response.status_code == 201
    return workspace_id, lead_response.json()["id"], credential


def _install_gateway_factory(monkeypatch, fake: FakeLLM, models: list[str]) -> None:
    def build_gateway(session, settings):
        def build_client(_settings: Settings, *, model: str) -> LLMClient:
            models.append(model)
            return fake

        return AIInvocationGateway(session, settings, llm_builder=build_client)

    monkeypatch.setattr("app.services.inbound_integrations.AIInvocationGateway", build_gateway)


def _usage_rows() -> list[AIInvocationUsage]:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        return list(session.exec(select(AIInvocationUsage)).all())


def test_inbound_sales_uses_gateway_and_records_safe_usage(
    client,
    integration_account_factory,
    signed_webhook_request,
    monkeypatch,
) -> None:
    workspace_id, lead_id, credential = _provision_inbound_sales(client, integration_account_factory)
    settings = _settings()
    app.dependency_overrides[get_settings] = lambda: settings
    fake = FakeLLM()
    models: list[str] = []
    _install_gateway_factory(monkeypatch, fake, models)
    payload = {
        "lead_id": lead_id,
        "channel": "website_chat",
        "content": "What is the monthly price?",
    }
    headers, body = signed_webhook_request(credential, payload)
    headers["X-Integration-Event-Id"] = "gateway-event-1"

    response = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    duplicate = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    rows = _usage_rows()

    assert response.status_code == 200
    assert response.json()["lead_id"] == lead_id
    assert response.json()["draft_reply"] == "Gateway-generated sales reply"
    assert response.json()["approval_id"] is not None
    assert models == ["standard-model"]
    assert len(fake.calls) == 1
    assert duplicate.json() == {
        "duplicate": True,
        "correlation_id": response.json()["correlation_id"],
    }
    assert len(rows) == 1
    stored = rows[0]
    assert stored.workspace_id == workspace_id
    assert stored.conversation_id == UUID(lead_id)
    assert stored.task_identifier == "sales.conversation.reply"
    assert stored.agent_identifier == "sales_conversation"
    assert stored.provider == "provider-b"
    assert stored.model == "standard-model"
    assert stored.status is AIInvocationStatus.SUCCESSFUL
    assert (stored.input_tokens, stored.output_tokens, stored.total_tokens) == (10, 5, 15)
    assert stored.estimated_cost == Decimal("0.000040")
    assert {
        "system_prompt",
        "user_prompt",
        "content",
        "api_key",
        "secret_reference",
        "signature",
    }.isdisjoint(stored.model_dump())


def test_inbound_sales_blocked_limit_never_builds_or_calls_llm(
    client,
    integration_account_factory,
    signed_webhook_request,
    monkeypatch,
) -> None:
    workspace_id, lead_id, credential = _provision_inbound_sales(client, integration_account_factory)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.get(Workspace, workspace_id)
        assert workspace is not None
        workspace.ai_invocation_limit = 0
        session.add(workspace)
        session.commit()
    app.dependency_overrides[get_settings] = _settings
    fake = FakeLLM()
    models: list[str] = []
    _install_gateway_factory(monkeypatch, fake, models)
    headers, body = signed_webhook_request(
        credential,
        {
            "lead_id": lead_id,
            "channel": "website_chat",
            "content": "What is the monthly price?",
        },
    )

    with pytest.raises(AIInvocationBlockedError, match="invocation limit"):
        client.post("/api/integrations/inbound-events", headers=headers, content=body)

    assert models == []
    assert fake.calls == []
    assert _usage_rows() == []


def test_inbound_sales_uses_workspace_policy_downgrade(
    client,
    integration_account_factory,
    signed_webhook_request,
    monkeypatch,
) -> None:
    workspace_id, lead_id, credential = _provision_inbound_sales(client, integration_account_factory)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.get(Workspace, workspace_id)
        assert workspace is not None
        workspace.ai_permitted_model_tiers = ["economy"]
        workspace.ai_model_tier_downgrade_mappings = {"standard": "economy"}
        session.add(workspace)
        session.commit()
    app.dependency_overrides[get_settings] = _settings
    fake = FakeLLM()
    models: list[str] = []
    _install_gateway_factory(monkeypatch, fake, models)
    headers, body = signed_webhook_request(
        credential,
        {
            "lead_id": lead_id,
            "channel": "website_chat",
            "content": "What is the monthly price?",
        },
    )

    response = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    rows = _usage_rows()

    assert response.status_code == 200
    assert models == ["economy-model"]
    assert len(fake.calls) == 1
    assert len(rows) == 1
    assert rows[0].workspace_id == workspace_id
    assert rows[0].provider == "provider-a"
    assert rows[0].model == "economy-model"
    assert rows[0].estimated_cost == Decimal("0.000025")


@pytest.mark.asyncio
async def test_sales_conversation_no_longer_uses_a_legacy_llm_without_gateway(
    client,
    integration_account_factory,
) -> None:
    workspace_id, lead_id, _ = _provision_inbound_sales(client, integration_account_factory)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.get(Workspace, workspace_id)
        lead = session.get(Lead, UUID(lead_id))
        assert workspace is not None
        assert lead is not None
        legacy_llm = FakeLLM()
        context = AgentContext(
            settings=_settings(),
            repository=SalesRepository(session),
            llm=legacy_llm,
            workspace=workspace,
            ai_invocation_gateway=None,
        )

        with pytest.raises(RuntimeError, match="AI invocation gateway"):
            await SalesConversationAgent(context).draft_reply(lead, "What is the monthly price?")

    assert legacy_llm.calls == []
