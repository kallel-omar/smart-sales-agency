from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import select

from app.config import Settings
from app.db import get_session
from app.departments.sales.handoff_policy import SalesHandoffSignals
from app.departments.sales.services import (
    SalesConversationTurnInput,
    SalesConversationTurnResult,
    SalesConversationTurnService,
)
from app.main import app
from app.models import ConversationMessage, Lead, SalesConversationHandoff, SalesStage, Workspace
from app.services.repository import NotFoundError, SalesRepository


class RecordingGateway:
    def __init__(self, reply: str = "Gateway Sales reply") -> None:
        self.reply = reply
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        return SimpleNamespace(content=self.reply)


def _settings() -> Settings:
    return Settings(
        llm_mode="openai_compatible",
        llm_api_key="test-key",
        require_human_approval=False,
    )


def _workspace_and_lead(session, slug: str = "turn-service") -> tuple[Workspace, Lead]:
    workspace = Workspace(slug=slug, name="Turn Service")
    lead = Lead(tenant_id=slug, full_name="Sarra Ben Ali", company_name="Example")
    session.add_all([workspace, lead])
    session.commit()
    session.refresh(workspace)
    session.refresh(lead)
    return workspace, lead


@pytest.mark.asyncio
async def test_turn_service_loads_bounded_history_once_then_persists_one_reply(client):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session)
        repository = SalesRepository(session)
        repository.add_message(
            ConversationMessage(
                lead_id=lead.id,
                direction="inbound",
                channel="website",
                stage=SalesStage.DISCOVERY,
                content="First customer answer",
            )
        )
        repository.add_message(
            ConversationMessage(
                lead_id=lead.id,
                direction="outbound",
                channel="website",
                stage=SalesStage.VALUE_PROPOSITION,
                content="First Sales response",
            )
        )
        gateway = RecordingGateway()

        result = await SalesConversationTurnService(
            repository=repository,
            settings=_settings(),
            workspace=workspace,
            ai_invocation_gateway=gateway,
        ).process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel="website",
                customer_message="What is the price?",
            )
        )

        history = repository.conversation_history(lead.id)

    assert result.ai_invoked is True
    assert result.handoff_required is False
    assert result.draft_reply == "Gateway Sales reply"
    assert len(gateway.requests) == 1
    assert "First customer answer" in gateway.requests[0].user_prompt
    assert "First Sales response" in gateway.requests[0].user_prompt
    assert gateway.requests[0].user_prompt.index("First customer answer") < gateway.requests[0].user_prompt.index(
        "First Sales response"
    )
    assert [message.direction for message in history] == ["inbound", "outbound", "inbound", "outbound"]
    assert [message.content for message in history].count("Gateway Sales reply") == 1


@pytest.mark.asyncio
async def test_active_handoff_skips_gateway_and_preserves_one_scoped_state(client):
    class ForbiddenGateway:
        async def invoke(self, request):  # pragma: no cover - must not be reached
            raise AssertionError(f"unexpected gateway request: {request}")

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session, "turn-handoff")
        repository = SalesRepository(session)
        repository.ensure_sales_handoff(
            workspace=workspace,
            lead=lead,
            reason_code="human_requested",
            explanation="A team member needs to assist with this request.",
        )

        result = await SalesConversationTurnService(
            repository=repository,
            settings=_settings(),
            workspace=workspace,
            ai_invocation_gateway=ForbiddenGateway(),
        ).process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel="website",
                customer_message="Can you confirm the terms?",
            )
        )
        handoffs = list(session.exec(select(SalesConversationHandoff)).all())

    assert result.ai_invoked is False
    assert result.handoff_required is True
    assert result.handoff_reason_code == "human_requested"
    assert len(handoffs) == 1


@pytest.mark.asyncio
async def test_gateway_failure_does_not_persist_a_successful_sales_turn(client):
    class FailingGateway:
        async def invoke(self, request):
            raise RuntimeError("provider unavailable")

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session, "turn-failure")
        repository = SalesRepository(session)
        service = SalesConversationTurnService(
            repository=repository,
            settings=_settings(),
            workspace=workspace,
            ai_invocation_gateway=FailingGateway(),
        )

        with pytest.raises(RuntimeError, match="provider unavailable"):
            await service.process(
                SalesConversationTurnInput(
                    lead_id=lead.id,
                    channel="website",
                    customer_message="What is the price?",
                )
            )

        assert repository.conversation_history(lead.id) == []


@pytest.mark.asyncio
async def test_turn_service_rejects_cross_workspace_lead_before_ai(client):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace_a, lead = _workspace_and_lead(session, "turn-a")
        workspace_b = Workspace(slug="turn-b", name="Turn B")
        session.add(workspace_b)
        session.commit()
        session.refresh(workspace_b)
        repository = SalesRepository(session)

        with pytest.raises(NotFoundError, match="Lead not found"):
            await SalesConversationTurnService(
                repository=repository,
                settings=_settings(),
                workspace=workspace_b,
                ai_invocation_gateway=RecordingGateway(),
            ).process(
                SalesConversationTurnInput(
                    lead_id=lead.id,
                    channel="website",
                    customer_message="What is the price?",
                    handoff_signals=SalesHandoffSignals(human_requested=True),
                )
            )


def test_reply_route_delegates_to_turn_service(client, monkeypatch):
    workspace_response = client.post(
        "/api/workspaces",
        json={"slug": "turn-route", "name": "Turn Route"},
    )
    assert workspace_response.status_code == 201
    lead_id = uuid4()
    expected = SalesConversationTurnResult(
        lead_id=lead_id,
        detected_stage=SalesStage.DISCOVERY,
        draft_reply="Delegated reply",
        approval_id=None,
    )

    async def process(self, source):
        assert source.lead_id == lead_id
        assert source.customer_message == "Need help"
        assert source.channel == "website"
        return expected

    monkeypatch.setattr(SalesConversationTurnService, "process", process)

    response = client.post(
        f"/api/conversations/{lead_id}/reply",
        headers={"X-Workspace-Slug": "turn-route"},
        json={"channel": "website", "content": "Need help"},
    )

    assert response.status_code == 200
    assert response.json()["draft_reply"] == "Delegated reply"
