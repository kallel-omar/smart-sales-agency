from uuid import UUID

import pytest
from sqlmodel import select

from app.config import Settings
from app.db import get_session
from app.departments.sales.handoff_policy import SalesHandoffSignals
from app.departments.sales.services import (
    DirectSalesConversationTurnService,
    SalesConversationTurnInput,
    SalesConversationTurnResult,
    SalesConversationTurnService,
)
from app.models import (
    AIInvocationUsage,
    ApprovalRequest,
    ConversationMessage,
    DirectConversationTurnReceipt,
    DirectConversationTurnReceiptStatus,
    Lead,
    SalesStage,
    Workspace,
)
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.llm import LLMCompletion
from app.services.repository import SalesRepository


def _create_workspace_and_lead(client, slug: str) -> tuple[UUID, UUID]:
    workspace = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.title()},
    )
    assert workspace.status_code == 201
    lead = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": slug},
        json={
            "tenant_id": slug,
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
            "source": "website",
        },
    )
    assert lead.status_code == 201
    return UUID(workspace.json()["id"]), UUID(lead.json()["id"])


def _reply(client, workspace_slug: str, lead_id: UUID, content: str, key: str | None = None):
    headers = {"X-Workspace-Slug": workspace_slug}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        f"/api/conversations/{lead_id}/reply",
        headers=headers,
        json={"channel": "website", "content": content},
    )


def _rows(client, model):
    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        return list(session.exec(select(model)).all())


def test_no_key_preserves_normal_direct_turn_behavior(client):
    _, lead_id = _create_workspace_and_lead(client, "direct-no-key")

    first = _reply(client, "direct-no-key", lead_id, "What is the price?")
    second = _reply(client, "direct-no-key", lead_id, "What is the price?")

    assert first.status_code == second.status_code == 200
    assert "duplicate" not in first.json()
    assert "duplicate" not in second.json()
    assert len(_rows(client, ConversationMessage)) == 2
    assert len(_rows(client, ApprovalRequest)) == 2
    assert _rows(client, DirectConversationTurnReceipt) == []


def test_completed_duplicate_replays_safe_result_without_new_persistence(client):
    _, lead_id = _create_workspace_and_lead(client, "direct-replay")

    first = _reply(client, "direct-replay", lead_id, "What is the price?", "turn-1")
    duplicate = _reply(client, "direct-replay", lead_id, "What is the price?", "turn-1")

    assert first.status_code == duplicate.status_code == 200
    assert "duplicate" not in first.json()
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json() | {"duplicate": None} == first.json() | {"duplicate": None}
    assert len(_rows(client, ConversationMessage)) == 1
    assert len(_rows(client, ApprovalRequest)) == 1
    receipts = _rows(client, DirectConversationTurnReceipt)
    assert len(receipts) == 1
    assert receipts[0].status == DirectConversationTurnReceiptStatus.COMPLETED
    assert _rows(client, AIInvocationUsage) == []


def test_same_key_with_changed_turn_is_conflict_without_second_execution(client):
    _, lead_id = _create_workspace_and_lead(client, "direct-conflict")

    first = _reply(client, "direct-conflict", lead_id, "What is the price?", "turn-1")
    conflict = _reply(client, "direct-conflict", lead_id, "Can I have a discount?", "turn-1")

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert len(_rows(client, ConversationMessage)) == 1
    assert len(_rows(client, ApprovalRequest)) == 1


@pytest.mark.parametrize("key", ["", "   ", "a" * 201])
def test_invalid_direct_turn_idempotency_key_is_rejected(client, key):
    _, lead_id = _create_workspace_and_lead(client, f"direct-invalid-{len(key)}")

    response = _reply(client, f"direct-invalid-{len(key)}", lead_id, "What is the price?", key)

    assert response.status_code == 422
    assert _rows(client, ConversationMessage) == []
    assert _rows(client, DirectConversationTurnReceipt) == []


def test_same_key_is_independent_per_workspace_and_lead(client):
    _, lead_a = _create_workspace_and_lead(client, "direct-a")
    _, lead_b = _create_workspace_and_lead(client, "direct-b")

    first_a = _reply(client, "direct-a", lead_a, "What is the price?", "shared-key")
    first_b = _reply(client, "direct-b", lead_b, "What is the price?", "shared-key")
    second_a = _reply(client, "direct-a", lead_a, "What is the price?", "shared-key")

    assert first_a.status_code == first_b.status_code == second_a.status_code == 200
    assert second_a.json()["duplicate"] is True
    assert len(_rows(client, DirectConversationTurnReceipt)) == 2
    assert len(_rows(client, ConversationMessage)) == 2


def test_cross_workspace_cannot_reuse_a_direct_turn_receipt(client):
    _, lead_id = _create_workspace_and_lead(client, "direct-owned")
    workspace_b = client.post(
        "/api/workspaces",
        json={"slug": "direct-other", "name": "Direct Other"},
    )
    assert workspace_b.status_code == 201

    assert _reply(client, "direct-owned", lead_id, "What is the price?", "shared-key").status_code == 200
    hidden = _reply(client, "direct-other", lead_id, "What is the price?", "shared-key")

    assert hidden.status_code == 404
    assert len(_rows(client, DirectConversationTurnReceipt)) == 1


def test_in_progress_receipt_returns_conflict_before_a_turn_runs(client):
    workspace_id, lead_id = _create_workspace_and_lead(client, "direct-progress")
    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.get(Workspace, workspace_id)
        lead = session.get(Lead, lead_id)
        assert workspace is not None and lead is not None
        repository = SalesRepository(session)
        source = SalesConversationTurnInput(
            lead_id=lead_id,
            channel="website",
            customer_message="What is the price?",
        )
        repository.reserve_direct_conversation_turn_receipt(
            workspace=workspace,
            lead=lead,
            idempotency_key="in-progress",
            request_fingerprint=DirectSalesConversationTurnService.request_fingerprint(source),
        )

    response = _reply(client, "direct-progress", lead_id, "What is the price?", "in-progress")

    assert response.status_code == 409
    assert len(_rows(client, ConversationMessage)) == 0
    assert len(_rows(client, ApprovalRequest)) == 0


@pytest.mark.asyncio
async def test_failed_turn_releases_receipt_for_an_explicit_same_key_retry(client, monkeypatch):
    workspace_id, lead_id = _create_workspace_and_lead(client, "direct-failure")
    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.get(Workspace, workspace_id)
        assert workspace is not None
        repository = SalesRepository(session)
        service = DirectSalesConversationTurnService(
            repository=repository,
            settings=Settings(llm_mode="demo"),
            workspace=workspace,
        )
        source = SalesConversationTurnInput(
            lead_id=lead_id,
            channel="website",
            customer_message="What is the price?",
        )

        async def fail(self, source):
            raise RuntimeError("provider unavailable")

        monkeypatch.setattr(SalesConversationTurnService, "process", fail)
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await service.process(source, idempotency_key="retry-key")
        assert list(session.exec(select(DirectConversationTurnReceipt)).all()) == []

        async def succeed(self, source):
            return SalesConversationTurnResult(
                lead_id=source.lead_id,
                detected_stage=SalesStage.DISCOVERY,
                draft_reply="Safe reply",
                approval_id=None,
            )

        monkeypatch.setattr(SalesConversationTurnService, "process", succeed)
        completed = await service.process(source, idempotency_key="retry-key")
        replayed = await service.process(source, idempotency_key="retry-key")

    assert completed.duplicate is False
    assert replayed.duplicate is True
    assert replayed.turn_result.draft_reply == "Safe reply"


@pytest.mark.asyncio
async def test_deterministic_handoff_is_replayed_without_ai_or_second_handoff(client):
    class ForbiddenGateway:
        async def invoke(self, request):  # pragma: no cover - must never run
            raise AssertionError("gateway should not run")

    workspace_id, lead_id = _create_workspace_and_lead(client, "direct-handoff")
    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.get(Workspace, workspace_id)
        lead = session.get(Lead, lead_id)
        assert workspace is not None and lead is not None
        repository = SalesRepository(session)
        service = DirectSalesConversationTurnService(
            repository=repository,
            settings=Settings(llm_mode="openai_compatible", llm_api_key="test"),
            workspace=workspace,
            ai_invocation_gateway=ForbiddenGateway(),
        )
        source = SalesConversationTurnInput(
            lead_id=lead_id,
            channel="website",
            customer_message="Please let me speak to a human",
            handoff_signals=SalesHandoffSignals(human_requested=True),
        )

        first = await service.process(source, idempotency_key="handoff-key")
        duplicate = await service.process(source, idempotency_key="handoff-key")

        handoffs = repository.list_sales_handoffs(workspace, lead_id)
        usage = list(session.exec(select(AIInvocationUsage)).all())

    assert first.turn_result.handoff_required is True
    assert duplicate.duplicate is True
    assert len(handoffs) == 1
    assert usage == []


@pytest.mark.asyncio
async def test_completed_duplicate_uses_the_gateway_once_and_creates_one_usage_row(client):
    class FakeLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_with_metadata(self, system_prompt, user_prompt):
            self.calls += 1
            return LLMCompletion(content="Gateway reply", input_tokens=10, output_tokens=5)

    workspace_id, lead_id = _create_workspace_and_lead(client, "direct-ai-once")
    settings = Settings(
        llm_mode="openai_compatible",
        llm_api_key="test-key",
        require_human_approval=False,
        ai_model_tier_mappings={
            "standard": {"provider": "provider-a", "model": "standard-model"},
        },
        ai_model_pricing=[
            {
                "provider": "provider-a",
                "model": "standard-model",
                "input_cost_per_million_tokens": "1.00",
                "output_cost_per_million_tokens": "2.00",
            },
        ],
    )
    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.get(Workspace, workspace_id)
        assert workspace is not None
        fake = FakeLLM()
        gateway = AIInvocationGateway(
            session,
            settings,
            llm_builder=lambda _settings, *, model: fake,
        )
        service = DirectSalesConversationTurnService(
            repository=SalesRepository(session),
            settings=settings,
            workspace=workspace,
            ai_invocation_gateway=gateway,
        )
        source = SalesConversationTurnInput(
            lead_id=lead_id,
            channel="website",
            customer_message="What is the price?",
        )

        first = await service.process(source, idempotency_key="ai-once")
        duplicate = await service.process(source, idempotency_key="ai-once")
        usage = list(session.exec(select(AIInvocationUsage)).all())
        messages = list(session.exec(select(ConversationMessage)).all())

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert fake.calls == 1
    assert len(usage) == 1
    assert len(messages) == 2
