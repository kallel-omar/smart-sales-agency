from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db import get_session
from app.main import app
from app.models import AIInvocationStatus, Workspace
from app.services.ai_invocation_usage import AIInvocationUsageService
from app.services.ai_model_pricing import (
    AIModelPricingCatalog,
    AIModelPricingValidationError,
)


def _workspace(client, slug: str) -> dict:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.title()})
    assert response.status_code == 201
    return response.json()


def _settings() -> Settings:
    return Settings(
        ai_model_pricing=[
            {
                "provider": "provider_a",
                "model": "model-a",
                "input_cost_per_million_tokens": "1.20",
                "output_cost_per_million_tokens": "4.80",
            }
        ]
    )


def test_provider_model_pricing_uses_separate_decimal_rates_without_io(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.build_llm",
        lambda settings: (_ for _ in ()).throw(AssertionError("pricing must not build an LLM")),
    )
    catalog = AIModelPricingCatalog(_settings().ai_model_pricing)

    estimate = catalog.estimate(
        provider="provider_a", model="model-a", input_tokens=2_500_000, output_tokens=500_000
    )

    assert estimate.pricing_known is True
    assert estimate.input_cost == Decimal("3.00")
    assert estimate.output_cost == Decimal("2.40")
    assert estimate.estimated_cost == Decimal("5.40")


def test_known_zero_tokens_and_unknown_pricing_are_explicit():
    catalog = AIModelPricingCatalog(_settings().ai_model_pricing)

    zero = catalog.estimate(provider="provider_a", model="model-a", input_tokens=0, output_tokens=0)
    unknown = catalog.estimate(provider="provider_a", model="other", input_tokens=12, output_tokens=8)

    assert zero.pricing_known is True
    assert zero.estimated_cost == Decimal("0.00")
    assert unknown.pricing_known is False
    assert unknown.estimated_cost is None
    assert unknown.input_cost is None
    assert unknown.output_cost is None


def test_invalid_pricing_and_negative_tokens_are_rejected():
    with pytest.raises(ValidationError):
        Settings(
            ai_model_pricing=[
                {
                    "provider": "provider_a",
                    "model": "model-a",
                    "input_cost_per_million_tokens": 0.1,
                    "output_cost_per_million_tokens": "1",
                }
            ]
        )
    with pytest.raises(ValidationError):
        Settings(
            ai_model_pricing=[
                {
                    "provider": "provider_a",
                    "model": "model-a",
                    "input_cost_per_million_tokens": "-1",
                    "output_cost_per_million_tokens": "1",
                }
            ]
        )
    with pytest.raises(ValidationError, match="unique"):
        Settings(ai_model_pricing=[*_settings().ai_model_pricing, *_settings().ai_model_pricing])
    with pytest.raises(AIModelPricingValidationError, match="Input tokens"):
        AIModelPricingCatalog(_settings().ai_model_pricing).estimate(
            provider="provider_a", model="model-a", input_tokens=-1, output_tokens=0
        )


def test_usage_recording_calculates_cost_and_read_remains_safe(client):
    workspace = _workspace(client, "ai-cost-known")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored_workspace = session.get(Workspace, UUID(workspace["id"]))
        usage = AIInvocationUsageService.from_settings(session, _settings()).record(
            stored_workspace,
            task_identifier="sales.reply",
            agent_identifier="sales",
            provider="provider_a",
            model="model-a",
            input_tokens=1_000_000,
            output_tokens=500_000,
            latency_ms=30,
            status=AIInvocationStatus.SUCCESSFUL,
        )
        usage_id = usage.id
        assert usage.total_tokens == 1_500_000
        assert usage.estimated_cost == Decimal("3.60000000")
        assert usage.pricing_known is True

    response = client.get("/api/integrations/ai-usage", headers={"X-Workspace-Slug": "ai-cost-known"})
    assert response.status_code == 200
    assert response.json()[0]["id"] == str(usage_id)
    assert response.json()[0]["estimated_cost"] == "3.60000000"
    assert response.json()[0]["pricing_known"] is True
    assert "prompt" not in response.text
    assert "response" not in response.text
    assert "api_key" not in response.text


def test_unknown_pricing_and_workspace_scoped_aggregate(client):
    workspace_a = _workspace(client, "ai-cost-summary-a")
    workspace_b = _workspace(client, "ai-cost-summary-b")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        a = session.get(Workspace, UUID(workspace_a["id"]))
        b = session.get(Workspace, UUID(workspace_b["id"]))
        service = AIInvocationUsageService.from_settings(session, _settings())
        service.record(
            a, task_identifier="sales.reply", agent_identifier="sales", provider="provider_a",
            model="model-a", input_tokens=1_000_000, output_tokens=500_000, latency_ms=1,
            status=AIInvocationStatus.SUCCESSFUL,
        )
        unknown = service.record(
            a, task_identifier="sales.reply", agent_identifier="sales", provider="provider_a",
            model="unpriced", input_tokens=7, output_tokens=3, latency_ms=1,
            status=AIInvocationStatus.FAILED,
        )
        service.record(
            b, task_identifier="sales.reply", agent_identifier="sales", provider="provider_a",
            model="model-a", input_tokens=99, output_tokens=1, latency_ms=1,
            status=AIInvocationStatus.SUCCESSFUL,
        )
        assert unknown.estimated_cost is None
        assert unknown.pricing_known is False

    response = client.get(
        "/api/integrations/ai-usage/summary",
        headers={"X-Workspace-Slug": "ai-cost-summary-a"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "invocation_count": 2,
        "successful_invocation_count": 1,
        "failed_invocation_count": 1,
        "input_tokens": 1_000_007,
        "output_tokens": 500_003,
        "total_tokens": 1_500_010,
        "known_estimated_spend": "3.60000000",
        "unknown_pricing_invocation_count": 1,
    }
