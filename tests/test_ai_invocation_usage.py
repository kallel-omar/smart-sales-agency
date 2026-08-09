from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlmodel import Session

from app.db import get_session
from app.main import app
from app.models import AIInvocationStatus, AIInvocationUsage, Workspace, utc_now
from app.services.ai_invocation_usage import (
    AIInvocationUsageService,
    AIInvocationUsageValidationError,
)


def _workspace(client, slug: str) -> dict:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.title()})
    assert response.status_code == 201
    return response.json()


def test_records_decimal_safe_usage_and_safe_workspace_read(client):
    workspace = _workspace(client, "ai-usage-a")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored_workspace = session.get(Workspace, UUID(workspace["id"]))
        usage = AIInvocationUsageService(session).record(
            stored_workspace,
            task_identifier="sales.reply",
            agent_identifier="sales_conversation",
            provider="provider_a",
            model="model-a",
            input_tokens=12,
            output_tokens=8,
            latency_ms=44,
            estimated_cost=Decimal("0.00125000"),
            status=AIInvocationStatus.SUCCESSFUL,
        )
        assert usage.total_tokens == 20
        assert usage.estimated_cost == Decimal("0.00125000")

    response = client.get("/api/integrations/ai-usage", headers={"X-Workspace-Slug": "ai-usage-a"})
    assert response.status_code == 200
    assert response.json()[0] == {
        "id": str(usage.id),
        "workspace_id": workspace["id"],
        "conversation_id": None,
        "task_identifier": "sales.reply",
        "agent_identifier": "sales_conversation",
        "provider": "provider_a",
        "model": "model-a",
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
        "latency_ms": 44,
        "estimated_cost": "0.00125000",
        "pricing_known": True,
        "status": "successful",
        "created_at": usage.created_at.isoformat().replace("+00:00", "Z"),
    }
    assert "prompt" not in response.text
    assert "response" not in response.text
    assert "api_key" not in response.text


def test_usage_is_workspace_scoped_and_deterministically_ordered(client):
    workspace_a = _workspace(client, "ai-usage-order-a")
    workspace_b = _workspace(client, "ai-usage-order-b")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        a = session.get(Workspace, UUID(workspace_a["id"]))
        b = session.get(Workspace, UUID(workspace_b["id"]))
        service = AIInvocationUsageService(session)
        older = service.record(
            a, task_identifier="task", agent_identifier="agent", provider="provider", model="model",
            input_tokens=1, output_tokens=2, latency_ms=3, status=AIInvocationStatus.FAILED,
            created_at=utc_now() - timedelta(minutes=1),
        )
        newer = service.record(
            a, task_identifier="task", agent_identifier="agent", provider="provider", model="model",
            input_tokens=2, output_tokens=3, latency_ms=4, status=AIInvocationStatus.SUCCESSFUL,
        )
        older_id = older.id
        newer_id = newer.id
        service.record(
            b, task_identifier="task", agent_identifier="agent", provider="provider", model="model",
            input_tokens=9, output_tokens=9, latency_ms=9, status=AIInvocationStatus.SUCCESSFUL,
        )

    response = client.get("/api/integrations/ai-usage", headers={"X-Workspace-Slug": "ai-usage-order-a"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(newer_id), str(older_id)]


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "latency_ms", "cost"),
    [(-1, 0, 0, None), (0, -1, 0, None), (0, 0, -1, None), (0, 0, 0, 0.1)],
)
def test_usage_validation_rejects_noncanonical_values(client, input_tokens, output_tokens, latency_ms, cost):
    workspace = _workspace(client, f"ai-usage-invalid-{input_tokens}-{output_tokens}-{latency_ms}")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored_workspace = session.get(Workspace, UUID(workspace["id"]))
        with pytest.raises(AIInvocationUsageValidationError):
            AIInvocationUsageService(session).record(
                stored_workspace,
                task_identifier="task",
                agent_identifier="agent",
                provider="provider",
                model="model",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                estimated_cost=cost,
                status=AIInvocationStatus.SUCCESSFUL,
            )
        assert session.query(AIInvocationUsage).count() == 0
