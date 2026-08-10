from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlmodel import select

from app.api.dependencies import get_settings
from app.config import Settings
from app.db import get_session
from app.main import app
from app.models import AIInvocationStatus, AIInvocationUsage, Workspace
from app.services.ai_invocation_gateway import (
    AIInvocationAccountingError,
    AIInvocationBlockedError,
    AIInvocationGateway,
    AIInvocationGatewayRequest,
    AIInvocationProviderMetadataError,
)
from app.services.ai_model_routing import AIPremiumJustification, AIModelRoutingTask
from app.services.ai_model_tiers import AIModelTierResolutionError
from app.services.llm import LLMClient, LLMCompletion


class FakeLLM(LLMClient):
    def __init__(
        self,
        *,
        content: str = "generated reply",
        input_tokens: object = 12,
        output_tokens: object = 8,
        total_tokens: object = None,
        error: Exception | None = None,
    ) -> None:
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return (await self.complete_with_metadata(system_prompt, user_prompt)).content

    async def complete_with_metadata(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        self.calls.append((system_prompt, user_prompt))
        if self.error is not None:
            raise self.error
        return LLMCompletion(
            content=self.content,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
        )


def _settings(*, premium_enabled: bool = False, mappings: dict | None = None, pricing: list | None = None) -> Settings:
    return Settings(
        auth_token_secret="test-auth-token-secret-32-byte-value",
        ai_model_routing_premium_enabled=premium_enabled,
        ai_model_tier_mappings=mappings
        or {
            "economy": {"provider": "provider-a", "model": "economy-model"},
            "standard": {"provider": "provider-b", "model": "standard-model"},
            "premium": {"provider": "provider-c", "model": "premium-model"},
        },
        ai_model_pricing=pricing
        if pricing is not None
        else [
            {
                "provider": "provider-b",
                "model": "standard-model",
                "input_cost_per_million_tokens": "2.00",
                "output_cost_per_million_tokens": "4.00",
            }
        ],
    )


def _stored_workspace(client, slug: str = "gateway-workspace") -> tuple[object, Workspace]:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.title()})
    assert response.status_code == 201
    session = next(app.dependency_overrides[get_session]())
    workspace = session.get(Workspace, UUID(response.json()["id"]))
    assert workspace is not None
    return session, workspace


def _request(workspace: Workspace, **kwargs) -> AIInvocationGatewayRequest:
    values = {
        "workspace": workspace,
        "task": AIModelRoutingTask.SALES_CONVERSATION,
        "task_identifier": "sales.conversation.reply",
        "agent_identifier": "sales_conversation",
        "system_prompt": "system prompt must stay transient",
        "user_prompt": "customer prompt must stay transient",
    }
    values.update(kwargs)
    return AIInvocationGatewayRequest(**values)


def _gateway(session, settings: Settings, fake: FakeLLM, built_models: list[str]) -> AIInvocationGateway:
    def builder(_settings: Settings, *, model: str) -> LLMClient:
        built_models.append(model)
        return fake

    return AIInvocationGateway(session, settings, llm_builder=builder)


@pytest.mark.asyncio
async def test_deterministic_route_does_not_build_or_invoke_an_llm(client):
    session, workspace = _stored_workspace(client)
    try:
        fake = FakeLLM()
        built_models: list[str] = []
        result = await _gateway(session, _settings(), fake, built_models).invoke(
            _request(workspace, task=AIModelRoutingTask.DETERMINISTIC)
        )
    finally:
        session.close()

    assert result.invoked is False
    assert result.content is None
    assert built_models == []
    assert fake.calls == []
    assert result.usage is None
    assert list(session.exec(select(AIInvocationUsage))) == []


@pytest.mark.asyncio
async def test_standard_and_economy_routes_use_their_resolved_models(client):
    session, workspace = _stored_workspace(client)
    try:
        standard_fake = FakeLLM(input_tokens=2, output_tokens=3)
        standard_models: list[str] = []
        standard = await _gateway(session, _settings(), standard_fake, standard_models).invoke(
            _request(workspace)
        )

        economy_fake = FakeLLM(input_tokens=1, output_tokens=1)
        economy_models: list[str] = []
        economy = await _gateway(session, _settings(), economy_fake, economy_models).invoke(
            _request(workspace, task=AIModelRoutingTask.CLASSIFICATION)
        )
    finally:
        session.close()

    assert standard.selection.provider == "provider-b"
    assert standard_models == ["standard-model"]
    assert economy.selection.provider == "provider-a"
    assert economy_models == ["economy-model"]


@pytest.mark.asyncio
async def test_explicit_premium_route_requires_policy_permission(client):
    session, workspace = _stored_workspace(client)
    try:
        premium_fake = FakeLLM()
        models: list[str] = []
        result = await _gateway(session, _settings(premium_enabled=True), premium_fake, models).invoke(
            _request(
                workspace,
                task=AIModelRoutingTask.HIGH_VALUE_CASE,
                premium_justification=AIPremiumJustification.HIGH_VALUE_CASE,
            )
        )
        fallback_models: list[str] = []
        fallback = await _gateway(session, _settings(premium_enabled=False), FakeLLM(), fallback_models).invoke(
            _request(
                workspace,
                task=AIModelRoutingTask.HIGH_VALUE_CASE,
                premium_justification=AIPremiumJustification.HIGH_VALUE_CASE,
            )
        )
    finally:
        session.close()

    assert result.selection.model == "premium-model"
    assert models == ["premium-model"]
    assert fallback.selection.model == "standard-model"
    assert fallback_models == ["standard-model"]


@pytest.mark.asyncio
async def test_blocked_limit_never_builds_or_invokes_an_llm(client):
    session, workspace = _stored_workspace(client)
    try:
        workspace.ai_invocation_limit = 0
        session.add(workspace)
        session.commit()
        fake = FakeLLM()
        models: list[str] = []
        with pytest.raises(AIInvocationBlockedError, match="invocation limit"):
            await _gateway(session, _settings(), fake, models).invoke(_request(workspace))
    finally:
        session.close()

    assert models == []
    assert fake.calls == []
    assert list(session.exec(select(AIInvocationUsage))) == []


@pytest.mark.asyncio
async def test_workspace_downgrade_uses_the_downgraded_tier_model(client):
    session, workspace = _stored_workspace(client)
    try:
        workspace.ai_permitted_model_tiers = ["standard"]
        workspace.ai_model_tier_downgrade_mappings = {"premium": "standard"}
        session.add(workspace)
        session.commit()
        fake = FakeLLM()
        models: list[str] = []
        result = await _gateway(session, _settings(premium_enabled=True), fake, models).invoke(
            _request(
                workspace,
                task=AIModelRoutingTask.COMPLEX_REASONING,
                premium_justification=AIPremiumJustification.COMPLEX_CASE,
            )
        )
    finally:
        session.close()

    assert result.limit_decision.outcome.value == "downgraded"
    assert result.selection.model == "standard-model"
    assert models == ["standard-model"]


@pytest.mark.asyncio
async def test_missing_final_mapping_fails_before_client_construction(client):
    session, workspace = _stored_workspace(client)
    try:
        fake = FakeLLM()
        models: list[str] = []
        settings = _settings(mappings={"economy": {"provider": "provider-a", "model": "economy-model"}})
        with pytest.raises(AIModelTierResolutionError, match="standard"):
            await _gateway(session, settings, fake, models).invoke(_request(workspace))
    finally:
        session.close()

    assert models == []
    assert fake.calls == []


@pytest.mark.asyncio
async def test_successful_usage_records_actual_tokens_cost_and_no_sensitive_content(client):
    session, workspace = _stored_workspace(client)
    try:
        result = await _gateway(session, _settings(), FakeLLM(input_tokens=10, output_tokens=5), []).invoke(
            _request(workspace)
        )
        assert result.usage is not None
        stored = result.usage
    finally:
        session.close()

    assert stored.status is AIInvocationStatus.SUCCESSFUL
    assert (stored.input_tokens, stored.output_tokens, stored.total_tokens) == (10, 5, 15)
    assert stored.estimated_cost == Decimal("0.000040")
    assert stored.latency_ms >= 0
    assert len(list(session.exec(select(AIInvocationUsage)))) == 1
    serialized = stored.model_dump()
    assert "system_prompt" not in serialized
    assert "user_prompt" not in serialized
    assert "api_key" not in serialized


@pytest.mark.asyncio
async def test_unknown_pricing_and_provider_failure_are_recorded_safely(client):
    session, workspace = _stored_workspace(client)
    try:
        unknown = await _gateway(
            session,
            _settings(pricing=[]),
            FakeLLM(input_tokens=3, output_tokens=4),
            [],
        ).invoke(_request(workspace))
        assert unknown.usage is not None

        with pytest.raises(RuntimeError, match="provider unavailable"):
            await _gateway(
                session,
                _settings(),
                FakeLLM(error=RuntimeError("provider unavailable")),
                [],
            ).invoke(_request(workspace))
        rows = list(session.exec(select(AIInvocationUsage)))
    finally:
        session.close()

    assert unknown.usage.estimated_cost is None
    failed_rows = [row for row in rows if row.status is AIInvocationStatus.FAILED]
    assert len(failed_rows) == 1
    failed = failed_rows[0]
    assert (failed.input_tokens, failed.output_tokens, failed.total_tokens) == (None, None, None)


@pytest.mark.asyncio
async def test_unavailable_provider_usage_is_persisted_as_unknown_not_zero(client):
    session, workspace = _stored_workspace(client, "gateway-unknown-token-usage")
    try:
        result = await _gateway(
            session,
            _settings(),
            FakeLLM(input_tokens=None, output_tokens=None),
            [],
        ).invoke(_request(workspace))
        assert result.usage is not None
        stored = result.usage
    finally:
        session.close()

    assert (stored.input_tokens, stored.output_tokens, stored.total_tokens) == (None, None, None)
    assert stored.estimated_cost is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "total_tokens"),
    [(-1, 1, None), (1, "invalid", None), (1, 2, 99), (True, 2, None)],
)
async def test_malformed_provider_usage_metadata_is_rejected_and_recorded_as_failed(
    client,
    input_tokens,
    output_tokens,
    total_tokens,
):
    session, workspace = _stored_workspace(
        client,
        f"gateway-malformed-{str(input_tokens).lower()}-{str(output_tokens).lower()}-{total_tokens}",
    )
    try:
        fake = FakeLLM(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
        with pytest.raises(AIInvocationProviderMetadataError, match="invalid usage metadata"):
            await _gateway(session, _settings(), fake, []).invoke(_request(workspace))
        rows = list(session.exec(select(AIInvocationUsage)))
    finally:
        session.close()

    assert fake.calls
    assert len(rows) == 1
    assert rows[0].status is AIInvocationStatus.FAILED
    assert (rows[0].input_tokens, rows[0].output_tokens, rows[0].total_tokens) == (
        None,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_successful_provider_call_raises_if_exactly_once_accounting_write_fails(client, monkeypatch):
    session, workspace = _stored_workspace(client, "gateway-accounting-write-failure")
    try:
        fake = FakeLLM()
        gateway = _gateway(session, _settings(), fake, [])
        record = Mock(side_effect=RuntimeError("database unavailable"))
        monkeypatch.setattr(gateway._usage_service, "record", record)

        with pytest.raises(AIInvocationAccountingError, match="accounting could not be completed"):
            await gateway.invoke(_request(workspace))
        assert list(session.exec(select(AIInvocationUsage))) == []
    finally:
        session.close()

    assert len(fake.calls) == 1
    assert record.call_count == 1


@pytest.mark.asyncio
async def test_failed_provider_call_preserves_original_error_when_failure_accounting_fails(
    client,
    monkeypatch,
):
    session, workspace = _stored_workspace(client, "gateway-failure-accounting-write-failure")
    try:
        fake = FakeLLM(error=RuntimeError("provider unavailable"))
        gateway = _gateway(session, _settings(), fake, [])
        record = Mock(side_effect=RuntimeError("database unavailable"))
        monkeypatch.setattr(gateway._usage_service, "record", record)

        with pytest.raises(RuntimeError, match="provider unavailable"):
            await gateway.invoke(_request(workspace))
    finally:
        session.close()

    assert len(fake.calls) == 1
    assert record.call_count == 1


@pytest.mark.asyncio
async def test_gateway_workspace_usage_isolation(client):
    session, workspace_a = _stored_workspace(client, "gateway-a")
    try:
        response = client.post("/api/workspaces", json={"slug": "gateway-b", "name": "Gateway B"})
        workspace_b = session.get(Workspace, UUID(response.json()["id"]))
        assert workspace_b is not None
        workspace_a.ai_invocation_limit = 1
        workspace_b.ai_invocation_limit = 1
        session.add(workspace_a)
        session.add(workspace_b)
        session.commit()
        await _gateway(session, _settings(), FakeLLM(), []).invoke(_request(workspace_a))
        with pytest.raises(AIInvocationBlockedError):
            await _gateway(session, _settings(), FakeLLM(), []).invoke(_request(workspace_a))
        result_b = await _gateway(session, _settings(), FakeLLM(), []).invoke(_request(workspace_b))
    finally:
        session.close()

    assert result_b.invoked is True


@pytest.mark.asyncio
async def test_sales_reply_route_uses_the_gateway_for_non_demo_calls(client, monkeypatch):
    workspace_response = client.post("/api/workspaces", json={"slug": "gateway-route", "name": "Gateway Route"})
    lead_response = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": "gateway-route"},
        json={
            "tenant_id": "gateway-route",
            "full_name": "Customer",
            "company_name": "Example",
            "source": "manual",
        },
    )
    configured = _settings()
    configured.llm_mode = "openai_compatible"
    configured.llm_api_key = "test-key"
    app.dependency_overrides[get_settings] = lambda: configured
    invoke = AsyncMock(return_value=Mock(content="gateway reply"))
    monkeypatch.setattr("app.services.ai_invocation_gateway.AIInvocationGateway.invoke", invoke)

    response = client.post(
        f"/api/conversations/{lead_response.json()['id']}/reply",
        headers={"X-Workspace-Slug": "gateway-route"},
        json={"channel": "web", "content": "How much does it cost?"},
    )

    assert workspace_response.status_code == 201
    assert response.status_code == 200
    assert response.json()["draft_reply"] == "gateway reply"
    invoke.assert_awaited_once()
