from types import SimpleNamespace

import pytest
from sqlmodel import select

from app.config import Settings
from app.db import get_session
from app.departments.sales.handoff_policy import (
    SalesCommercialEscalationType,
    SalesHandoffSignals,
)
from app.departments.sales.services import (
    SalesConversationHandoffService,
    SalesConversationTurnInput,
    SalesConversationTurnService,
)
from app.main import app
from app.models import (
    AIInvocationUsage,
    ApprovalRequest,
    Lead,
    SalesConversationHandoff,
    SalesConversationHandoffStatus,
    SalesHandoffReasonCode,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.services.repository import NotFoundError, SalesRepository


class RecordingGateway:
    def __init__(self, reply: str = "Normal Sales reply") -> None:
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


def _workspace_and_lead(session, slug: str = "handoff-lifecycle") -> tuple[Workspace, Lead]:
    workspace = Workspace(slug=slug, name="Handoff Lifecycle")
    lead = Lead(tenant_id=slug, full_name="Sarra Ben Ali", company_name="Example")
    session.add_all([workspace, lead])
    session.commit()
    session.refresh(workspace)
    session.refresh(lead)
    return workspace, lead


def _add_fixture_membership(session, workspace: Workspace) -> None:
    user = session.exec(select(User).where(User.email == "fixture-operator@example.com")).one()
    session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceMemberRole.MEMBER,
        )
    )


@pytest.mark.asyncio
async def test_active_handoff_resolves_historically_and_later_turn_can_use_ai(client):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session)
        repository = SalesRepository(session)
        blocked_gateway = RecordingGateway()
        turn_service = SalesConversationTurnService(
            repository=repository,
            settings=_settings(),
            workspace=workspace,
            ai_invocation_gateway=blocked_gateway,
        )

        handoff_turn = await turn_service.process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel="website",
                customer_message="Please give me a custom discount.",
                handoff_signals=SalesHandoffSignals(
                    commercial_escalation=SalesCommercialEscalationType.UNSUPPORTED_DISCOUNT
                ),
            )
        )
        active_handoff = repository.get_sales_handoff(workspace, lead.id)
        assert active_handoff is not None
        assert active_handoff.status == SalesConversationHandoffStatus.ACTIVE
        assert handoff_turn.ai_invoked is False
        assert blocked_gateway.requests == []

        resolution = SalesConversationHandoffService(
            repository=repository,
            workspace=workspace,
        ).resolve_active_handoff(lead.id)
        resolved_handoff = repository.list_sales_handoffs(workspace, lead.id)[0]

        gateway = RecordingGateway("AI is available again")
        normal_turn = await SalesConversationTurnService(
            repository=repository,
            settings=_settings(),
            workspace=workspace,
            ai_invocation_gateway=gateway,
        ).process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel="website",
                customer_message="What is the standard price?",
            )
        )

        approvals = list(session.exec(select(ApprovalRequest)).all())
        usages = list(session.exec(select(AIInvocationUsage)).all())
        resolved_at = resolved_handoff.resolved_at
        resolved_reason_code = resolved_handoff.reason_code
        current_handoff = repository.get_sales_handoff(workspace, lead.id)

    assert resolution.status == SalesConversationHandoffStatus.RESOLVED
    assert resolution.resolved_at is not None
    assert resolved_at == resolution.resolved_at
    assert resolved_reason_code == SalesHandoffReasonCode.UNSUPPORTED_DISCOUNT_REQUEST
    assert current_handoff is None
    assert normal_turn.handoff_required is False
    assert normal_turn.ai_invoked is True
    assert normal_turn.draft_reply == "AI is available again"
    assert len(gateway.requests) == 1
    assert approvals == []
    assert usages == []


def test_resolution_is_explicit_scoped_and_repeated_transition_conflicts(client):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session, "handoff-resolve")
        _add_fixture_membership(session, workspace)
        repository = SalesRepository(session)
        active = repository.ensure_sales_handoff(
            workspace=workspace,
            lead=lead,
            reason_code=SalesHandoffReasonCode.HUMAN_REQUESTED,
            explanation="A team member needs to assist with this request.",
        )
        active_id = active.id
        lead_id = lead.id
        workspace_slug = workspace.slug

    response = client.post(
        f"/api/conversations/{lead_id}/handoff/resolve",
        headers={"X-Workspace-Slug": workspace_slug},
        json={"workspace_id": "not-an-authority", "resolved_by": "customer"},
    )
    repeated = client.post(
        f"/api/conversations/{lead_id}/handoff/resolve",
        headers={"X-Workspace-Slug": workspace_slug},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    assert response.json()["reason_code"] == "human_requested"
    assert response.json()["resolved_at"] is not None
    assert repeated.status_code == 409

    with next(session_dependency()) as session:
        repository = SalesRepository(session)
        resolved_workspace = next(
            item for item in session.exec(select(Workspace)).all() if item.slug == workspace_slug
        )
        history = repository.list_sales_handoffs(resolved_workspace, lead_id)
        assert len(history) == 1
        assert history[0].id == active_id
        assert history[0].status == SalesConversationHandoffStatus.RESOLVED
        assert history[0].reason_code == SalesHandoffReasonCode.HUMAN_REQUESTED
        assert list(session.exec(select(AIInvocationUsage)).all()) == []
        assert list(session.exec(select(ApprovalRequest)).all()) == []


def test_missing_and_cross_workspace_handoff_resolution_return_safe_not_found(client):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace_a, lead = _workspace_and_lead(session, "handoff-a")
        workspace_b = Workspace(slug="handoff-b", name="Handoff B")
        session.add(workspace_b)
        _add_fixture_membership(session, workspace_a)
        _add_fixture_membership(session, workspace_b)
        session.commit()
        session.refresh(workspace_b)
        repository = SalesRepository(session)
        repository.ensure_sales_handoff(
            workspace=workspace_a,
            lead=lead,
            reason_code=SalesHandoffReasonCode.HUMAN_REQUESTED,
            explanation="A team member needs to assist with this request.",
        )
        lead_id = lead.id
        workspace_a_slug = workspace_a.slug
        workspace_b_slug = workspace_b.slug

    cross_workspace = client.post(
        f"/api/conversations/{lead_id}/handoff/resolve",
        headers={"X-Workspace-Slug": workspace_b_slug},
    )
    missing = client.post(
        f"/api/conversations/{lead_id}/handoff/resolve",
        headers={"X-Workspace-Slug": workspace_a_slug},
    )

    assert cross_workspace.status_code == 404
    assert missing.status_code == 200

    with next(session_dependency()) as session:
        repository = SalesRepository(session)
        resolved_workspace_a = next(
            item for item in session.exec(select(Workspace)).all() if item.slug == workspace_a_slug
        )
        assert repository.get_sales_handoff(resolved_workspace_a, lead_id) is None

    missing_after_resolution = client.post(
        f"/api/conversations/{lead_id}/handoff/resolve",
        headers={"X-Workspace-Slug": workspace_a_slug},
    )
    assert missing_after_resolution.status_code == 409


def test_new_trigger_after_resolution_creates_new_active_historical_record(client):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session, "handoff-history")
        repository = SalesRepository(session)
        first = repository.ensure_sales_handoff(
            workspace=workspace,
            lead=lead,
            reason_code=SalesHandoffReasonCode.HUMAN_REQUESTED,
            explanation="A team member needs to assist with this request.",
        )
        SalesConversationHandoffService(
            repository=repository,
            workspace=workspace,
        ).resolve_active_handoff(lead.id)

        second = repository.ensure_sales_handoff(
            workspace=workspace,
            lead=lead,
            reason_code=SalesHandoffReasonCode.CUSTOM_PRICING_REQUIRED,
            explanation="A team member needs to review the requested commercial terms.",
        )
        repeated_active = repository.ensure_sales_handoff(
            workspace=workspace,
            lead=lead,
            reason_code=SalesHandoffReasonCode.CUSTOM_PRICING_REQUIRED,
            explanation="A team member needs to review the requested commercial terms.",
        )
        history = repository.list_sales_handoffs(workspace, lead.id)
        first_id = first.id
        second_id = second.id
        repeated_active_id = repeated_active.id
        history_states = [handoff.status for handoff in history]
        historical_reason_code = history[0].reason_code
        historical_resolved_at = history[0].resolved_at
        active_reason_code = history[1].reason_code

    assert first_id != second_id
    assert repeated_active_id == second_id
    assert history_states == [
        SalesConversationHandoffStatus.RESOLVED,
        SalesConversationHandoffStatus.ACTIVE,
    ]
    assert historical_reason_code == SalesHandoffReasonCode.HUMAN_REQUESTED
    assert historical_resolved_at is not None
    assert active_reason_code == SalesHandoffReasonCode.CUSTOM_PRICING_REQUIRED


def test_resolution_service_rejects_missing_and_cross_workspace_without_ai(client):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace_a, lead = _workspace_and_lead(session, "handoff-service-a")
        workspace_b = Workspace(slug="handoff-service-b", name="Handoff Service B")
        session.add(workspace_b)
        session.commit()
        session.refresh(workspace_b)
        repository = SalesRepository(session)

        with pytest.raises(NotFoundError, match="Sales handoff not found"):
            SalesConversationHandoffService(
                repository=repository,
                workspace=workspace_a,
            ).resolve_active_handoff(lead.id)

        with pytest.raises(NotFoundError, match="Lead not found"):
            SalesConversationHandoffService(
                repository=repository,
                workspace=workspace_b,
            ).resolve_active_handoff(lead.id)

        assert list(session.exec(select(SalesConversationHandoff)).all()) == []
