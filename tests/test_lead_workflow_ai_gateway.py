from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlmodel import select

from app.api.dependencies import get_settings
from app.config import Settings
from app.db import get_session
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.agents.lead_researcher import LeadResearchAgent
from app.main import app
from app.models import AIInvocationStatus, AIInvocationUsage, Lead, Workspace
from app.services.ai_invocation_gateway import (
    AIInvocationBlockedError,
    AIInvocationGateway,
)
from app.services.llm import LLMClient, LLMCompletion
from app.services.repository import SalesRepository


class FakeLLM(LLMClient):
    def __init__(self, *, content: str = "Generated research brief") -> None:
        self.content = content
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return (await self.complete_with_metadata(system_prompt, user_prompt)).content

    async def complete_with_metadata(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
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
            }
        ],
    )


def _workspace_and_lead(client, slug: str = "lead-gateway") -> tuple[object, Workspace, Lead]:
    workspace_response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.title()},
    )
    assert workspace_response.status_code == 201
    lead_response = client.post(
        "/api/leads",
        json={
            "tenant_id": slug,
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
            "job_title": "Sales Director",
            "website": "https://example.test",
            "notes": "Needs faster sales follow-up.",
            "source": "manual",
        },
    )
    assert lead_response.status_code == 201

    session = next(app.dependency_overrides[get_session]())
    workspace = session.get(Workspace, UUID(workspace_response.json()["id"]))
    lead = session.get(Lead, UUID(lead_response.json()["id"]))
    assert workspace is not None
    assert lead is not None
    return session, workspace, lead


def _gateway(session, settings: Settings, fake: FakeLLM, models: list[str]) -> AIInvocationGateway:
    def builder(_settings: Settings, *, model: str) -> LLMClient:
        models.append(model)
        return fake

    return AIInvocationGateway(session, settings, llm_builder=builder)


@pytest.mark.asyncio
async def test_lead_research_uses_gateway_economy_route_and_records_safe_usage(client) -> None:
    session, workspace, lead = _workspace_and_lead(client)
    try:
        workspace_id = workspace.id
        lead_id = lead.id
        settings = _settings()
        fake = FakeLLM()
        models: list[str] = []
        context = AgentContext(
            settings=settings,
            repository=SalesRepository(session),
            llm=None,
            workspace=workspace,
            ai_invocation_gateway=_gateway(session, settings, fake, models),
        )

        research = await LeadResearchAgent(context).run(lead)
        stored = session.exec(select(AIInvocationUsage)).one()
    finally:
        session.close()

    assert research["summary"] == "Generated research brief"
    assert research["pain_points"] == ["Review the generated brief before outreach"]
    assert research["opportunities"] == ["Prepare a personalized discovery message"]
    assert models == ["economy-model"]
    assert len(fake.calls) == 1
    assert stored.workspace_id == workspace_id
    assert stored.conversation_id == lead_id
    assert stored.task_identifier == "sales.lead_research"
    assert stored.agent_identifier == "lead_research"
    assert stored.provider == "provider-a"
    assert stored.model == "economy-model"
    assert stored.status is AIInvocationStatus.SUCCESSFUL
    assert (stored.input_tokens, stored.output_tokens, stored.total_tokens) == (10, 5, 15)
    assert stored.estimated_cost == Decimal("0.000025")
    assert {"system_prompt", "user_prompt", "content", "api_key", "llm_api_key"}.isdisjoint(
        stored.model_dump()
    )


@pytest.mark.asyncio
async def test_lead_research_blocked_by_workspace_policy_never_builds_or_calls_llm(client) -> None:
    session, workspace, lead = _workspace_and_lead(client)
    try:
        workspace.ai_invocation_limit = 0
        session.add(workspace)
        session.commit()
        settings = _settings()
        fake = FakeLLM()
        models: list[str] = []
        context = AgentContext(
            settings=settings,
            repository=SalesRepository(session),
            llm=None,
            workspace=workspace,
            ai_invocation_gateway=_gateway(session, settings, fake, models),
        )

        with pytest.raises(AIInvocationBlockedError, match="invocation limit"):
            await LeadResearchAgent(context).run(lead)
        rows = list(session.exec(select(AIInvocationUsage)))
    finally:
        session.close()

    assert models == []
    assert fake.calls == []
    assert rows == []


@pytest.mark.asyncio
async def test_lead_research_requires_gateway_instead_of_legacy_llm(client) -> None:
    session, workspace, lead = _workspace_and_lead(client)
    try:
        legacy_llm = Mock(spec=LLMClient)
        context = AgentContext(
            settings=_settings(),
            repository=SalesRepository(session),
            llm=legacy_llm,
            workspace=workspace,
            ai_invocation_gateway=None,
        )

        with pytest.raises(RuntimeError, match="AI invocation gateway"):
            await LeadResearchAgent(context).run(lead)
    finally:
        session.close()

    legacy_llm.complete.assert_not_called()


@pytest.mark.asyncio
async def test_lead_workflow_passes_server_resolved_workspace_to_gateway(client, monkeypatch) -> None:
    workspace_response = client.post(
        "/api/workspaces",
        json={"slug": "lead-route", "name": "Lead Route"},
    )
    lead_response = client.post(
        "/api/leads",
        json={
            "tenant_id": "lead-route",
            "full_name": "Customer",
            "company_name": "Example",
            "source": "manual",
        },
    )
    settings = _settings()
    app.dependency_overrides[get_settings] = lambda: settings
    gateway_workspace_ids: list[UUID] = []

    async def capture_workspace(request):
        gateway_workspace_ids.append(request.workspace.id)
        return Mock(content="Gateway research brief")

    invoke = AsyncMock(side_effect=capture_workspace)
    monkeypatch.setattr("app.services.ai_invocation_gateway.AIInvocationGateway.invoke", invoke)

    response = client.post(
        f"/api/workflows/{lead_response.json()['id']}/run",
        headers={"X-Workspace-Slug": "lead-route"},
    )

    assert workspace_response.status_code == 201
    assert response.status_code == 200
    assert response.json()["research_summary"] == "Gateway research brief"
    invoke.assert_awaited_once()
    request = invoke.await_args.args[0]
    assert gateway_workspace_ids == [UUID(workspace_response.json()["id"])]
    assert request.conversation_id == UUID(lead_response.json()["id"])
    assert request.task.value == "simple_summary"
    assert request.task_identifier == "sales.lead_research"
    assert request.agent_identifier == "lead_research"
