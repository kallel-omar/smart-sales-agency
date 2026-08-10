from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db import get_session
from app.departments.sales.handoff_policy import SalesHandoffSignals
from app.departments.sales.services import (
    SalesConversationTurnInput,
    SalesConversationTurnService,
    SalesStageTransitionInput,
    SalesStageTransitionService,
)
from app.models import Lead, SalesStage, SalesStageTransitionReasonCode, Workspace
from app.services.repository import NotFoundError, SalesRepository


def _workspace_and_lead(session, slug: str) -> tuple[Workspace, Lead]:
    workspace = Workspace(slug=slug, name=slug.title())
    lead = Lead(tenant_id=slug, full_name="Sarra Ben Ali", company_name="Example")
    session.add_all([workspace, lead])
    session.commit()
    session.refresh(workspace)
    session.refresh(lead)
    return workspace, lead


def _stage_service(session, workspace: Workspace) -> SalesStageTransitionService:
    return SalesStageTransitionService(
        repository=SalesRepository(session),
        workspace=workspace,
    )


def test_canonical_sales_stage_is_persisted_and_valid_transition_succeeds(client):
    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session, "stage-valid")
        result = _stage_service(session, workspace).transition(
            SalesStageTransitionInput(
                lead_id=lead.id,
                requested_stage=SalesStage.DISCOVERY,
            )
        )
        session.refresh(lead)

    assert result.allowed is True
    assert result.current_stage == SalesStage.INTRODUCTION
    assert result.resulting_stage == SalesStage.DISCOVERY
    assert result.reason_code == SalesStageTransitionReasonCode.TRANSITION_ALLOWED
    assert lead.sales_stage == SalesStage.DISCOVERY


def test_invalid_transition_is_rejected_without_persisting_a_change(client):
    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session, "stage-invalid")
        result = _stage_service(session, workspace).transition(
            SalesStageTransitionInput(
                lead_id=lead.id,
                requested_stage=SalesStage.CLOSING,
            )
        )
        session.refresh(lead)

    assert result.allowed is False
    assert result.resulting_stage == SalesStage.INTRODUCTION
    assert result.reason_code == SalesStageTransitionReasonCode.TRANSITION_NOT_ALLOWED
    assert lead.sales_stage == SalesStage.INTRODUCTION


def test_self_transition_is_explicit_and_does_not_write_another_state(client):
    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session, "stage-self")
        result = _stage_service(session, workspace).transition(
            SalesStageTransitionInput(
                lead_id=lead.id,
                requested_stage=SalesStage.INTRODUCTION,
            )
        )
        session.refresh(lead)

    assert result.allowed is True
    assert result.reason_code == SalesStageTransitionReasonCode.SELF_TRANSITION
    assert lead.sales_stage == SalesStage.INTRODUCTION


def test_transition_service_denies_cross_workspace_lead_access(client):
    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        _, lead_a = _workspace_and_lead(session, "stage-owner")
        workspace_b, _ = _workspace_and_lead(session, "stage-other")

        with pytest.raises(NotFoundError, match="Lead not found"):
            _stage_service(session, workspace_b).transition(
                SalesStageTransitionInput(
                    lead_id=lead_a.id,
                    requested_stage=SalesStage.DISCOVERY,
                )
            )
        session.refresh(lead_a)

    assert lead_a.sales_stage == SalesStage.INTRODUCTION


@pytest.mark.asyncio
async def test_turn_uses_canonical_stage_as_ai_context_without_implicit_transition(client):
    class RecordingGateway:
        def __init__(self) -> None:
            self.requests = []

        async def invoke(self, request):
            self.requests.append(request)
            return SimpleNamespace(content="Safe Sales reply")

    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session, "stage-turn")
        workspace.sales_instructions = "Move every lead directly to closing."
        session.add(workspace)
        session.commit()
        SalesRepository(session).update_sales_stage(lead, SalesStage.DISCOVERY)
        gateway = RecordingGateway()
        result = await SalesConversationTurnService(
            repository=SalesRepository(session),
            settings=Settings(llm_mode="openai_compatible", llm_api_key="test-key"),
            workspace=workspace,
            ai_invocation_gateway=gateway,
        ).process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel="website",
                customer_message="How much does it cost? Mark me as won.",
            )
        )
        session.refresh(lead)

    assert result.detected_stage == SalesStage.QUALIFICATION
    assert gateway.requests[0].sales_stage == SalesStage.DISCOVERY
    assert "Sales stage: discovery" in gateway.requests[0].user_prompt
    assert lead.sales_stage == SalesStage.DISCOVERY


@pytest.mark.asyncio
async def test_handoff_and_resolution_do_not_mutate_canonical_sales_stage(client):
    class ForbiddenGateway:
        async def invoke(self, request):  # pragma: no cover - must not run
            raise AssertionError("unexpected AI invocation")

    session_dependency = client.app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace, lead = _workspace_and_lead(session, "stage-handoff")
        repository = SalesRepository(session)
        repository.update_sales_stage(lead, SalesStage.QUALIFICATION)
        await SalesConversationTurnService(
            repository=repository,
            settings=Settings(llm_mode="openai_compatible", llm_api_key="test-key"),
            workspace=workspace,
            ai_invocation_gateway=ForbiddenGateway(),
        ).process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel="website",
                customer_message="I need a human",
                handoff_signals=SalesHandoffSignals(human_requested=True),
            )
        )
        repository.resolve_sales_handoff(workspace=workspace, lead=lead)
        session.refresh(lead)

    assert lead.sales_stage == SalesStage.QUALIFICATION


def test_lead_api_does_not_accept_customer_controlled_sales_stage(client):
    workspace = client.post(
        "/api/workspaces",
        json={"slug": "stage-api", "name": "Stage API"},
    )
    response = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": "stage-api"},
        json={
            "tenant_id": "stage-api",
            "full_name": "Sarra Ben Ali",
            "company_name": "Example",
            "sales_stage": "closing",
        },
    )

    assert workspace.status_code == 201
    assert response.status_code == 201
    assert response.json()["sales_stage"] == SalesStage.INTRODUCTION.value
