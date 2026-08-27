from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.core.agent_skills import AgentSkillRoleNotEligibleError
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.capabilities import BusinessCapabilityKey
from app.core.comment_triggers import CommentTriggerResult
from app.core.work_items import WorkItemStatus
from app.departments.sales.agents.follow_up import FollowUpAgent
from app.departments.sales.services.work_item_execution import (
    SalesWorkItemExecutionService,
)
from app.models import (
    AIEmployee,
    ApprovalRequest,
    ConversationMessage,
    FollowUpTask,
    IntegrationAccount,
    Lead,
    LeadStatus,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationActionType,
    SalesConversationHandoff,
    SalesHandoffReasonCode,
    SalesLanguage,
    WorkItem,
    Workspace,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employee_tool_access import AIEmployeeCapabilityToolAccessService
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.delivery_adapters import DeliveryAdapterRegistry, NoopDeliveryAdapter
from app.services.departments import DepartmentService
from app.services.follow_up_work_items import (
    FollowUpWorkItemMaterializationService,
    FollowUpWorkItemScopeError,
)
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def _settings() -> Settings:
    return Settings(environment="test", database_url="sqlite://", llm_mode="demo")


def _foundation(session: Session, slug: str, *, send_assignment: bool = True):
    workspace = Workspace(slug=slug, name=slug)
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    department = DepartmentService(session).ensure_sales_department(workspace)
    capabilities = CapabilityService(session)
    follow_capability = capabilities.ensure_for_department(
        workspace, department, BusinessCapabilityKey.FOLLOW_UP_LEAD
    )
    follow_employee = AIEmployeeService(session).create_for_department(
        workspace, department, AIEmployeeRoleKey.FOLLOW_UP
    )
    follow_assignment = AIEmployeeCapabilityAssignmentService(session).assign(
        workspace, follow_employee, follow_capability
    )
    send_capability = capabilities.ensure_for_department(
        workspace, department, BusinessCapabilityKey.SEND_MESSAGE
    )
    resolved_send_assignment = None
    if send_assignment:
        send_employee = AIEmployeeService(session).create_for_department(
            workspace, department, AIEmployeeRoleKey.SALES_CONVERSATION
        )
        resolved_send_assignment = AIEmployeeCapabilityAssignmentService(session).assign(
            workspace, send_employee, send_capability
        )
    lead = Lead(
        tenant_id=workspace.slug,
        full_name="Amina Trabelsi",
        company_name="Example Co",
        email="amina@example.com",
    )
    task = FollowUpTask(
        lead_id=lead.id,
        due_at=datetime.now(UTC) - timedelta(minutes=1),
        reason="Proposal follow-up",
    )
    session.add(lead)
    session.add(task)
    session.commit()
    session.refresh(lead)
    session.refresh(task)
    return SimpleNamespace(
        workspace=workspace,
        department=department,
        lead=lead,
        task=task,
        follow_assignment=follow_assignment,
        send_assignment=resolved_send_assignment,
    )


def _materialize(session: Session, state):
    work_item, decision = FollowUpWorkItemMaterializationService(session).materialize_due(
        state.workspace, state.task.id
    )
    assert work_item is not None
    return work_item, decision


def _account(session: Session, state) -> IntegrationAccount:
    account = IntegrationAccount(
        workspace_id=state.workspace.id,
        provider="facebook_messenger",
        external_account_id=f"page-{uuid4().hex}",
        secret_reference="FOLLOW_UP_TEST_SECRET",
        credential_hash=uuid4().hex,
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _configure_send(session: Session, work_item: WorkItem, account: IntegrationAccount):
    work_item.input = {
        **work_item.input,
        "integration_account_id": str(account.id),
        "channel": "facebook_messenger",
        "recipient": "external-recipient-1",
        "message": "Just checking whether you had any questions.",
    }
    session.add(work_item)
    session.commit()
    session.refresh(work_item)


def _configure_route(session: Session, work_item: WorkItem, account: IntegrationAccount):
    work_item.input = {
        **work_item.input,
        "integration_account_id": str(account.id),
        "channel": "facebook_messenger",
        "recipient": "external-recipient-1",
    }
    session.add(work_item)
    session.commit()
    session.refresh(work_item)


def _grant(session: Session, state, account, autonomy):
    AIEmployeeCapabilityToolAccessService(session).grant(
        state.workspace,
        state.send_assignment,
        account,
        OutboundIntegrationActionType.SEND_MESSAGE,
        autonomy,
    )


def _noop_delivery(monkeypatch):
    def from_settings(cls, session, settings, **kwargs):
        del cls, settings, kwargs
        return OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry({"facebook_messenger": NoopDeliveryAdapter()}),
        )

    monkeypatch.setattr(
        OutboundIntegrationDeliveryService,
        "from_settings",
        classmethod(from_settings),
    )


def test_due_task_materializes_once_and_routes_to_follow_up_assignment(session: Session):
    state = _foundation(session, "follow-up-materialize")

    first, decision = _materialize(session, state)
    repeated, repeated_decision = _materialize(session, state)

    assert first.id == repeated.id
    assert first.status == WorkItemStatus.ASSIGNED
    assert first.assignment_id == state.follow_assignment.id
    assert first.work_type == "sales_follow_up"
    assert first.input == {
        "follow_up_task_id": str(state.task.id),
        "lead_id": str(state.lead.id),
        "reason": state.task.reason,
        "scheduled_at": state.task.due_at.replace(tzinfo=UTC).isoformat(),
    }
    assert decision is not None and decision.routable
    assert repeated_decision is None
    assert len(session.exec(select(WorkItem)).all()) == 1


@pytest.mark.parametrize("state_kind", ["not_due", "cancelled", "completed"])
def test_ineligible_task_creates_no_work_item(session: Session, state_kind: str):
    state = _foundation(session, f"follow-up-{state_kind}")
    if state_kind == "not_due":
        state.task.due_at = datetime.now(UTC) + timedelta(days=1)
    else:
        state.task.status = state_kind
    session.add(state.task)
    session.commit()

    work_item, decision = FollowUpWorkItemMaterializationService(session).materialize_due(
        state.workspace, state.task.id
    )

    assert work_item is None and decision is None
    assert session.exec(select(WorkItem)).all() == []


def test_materialization_enforces_legacy_lead_workspace_ownership(session: Session):
    state = _foundation(session, "follow-up-owner")
    other = Workspace(slug="other-workspace", name="Other")
    session.add(other)
    session.commit()

    with pytest.raises(FollowUpWorkItemScopeError, match="does not belong"):
        FollowUpWorkItemMaterializationService(session).materialize_due(other, state.task.id)


def test_missing_follow_up_assignment_leaves_created_work_unresolved(session: Session):
    state = _foundation(session, "follow-up-no-route")
    session.delete(state.follow_assignment)
    session.commit()

    work_item, decision = FollowUpWorkItemMaterializationService(session).materialize_due(
        state.workspace, state.task.id
    )

    assert work_item is not None and work_item.status == WorkItemStatus.CREATED
    assert decision is not None and not decision.routable
    assert state.task.status == "pending"


@pytest.mark.asyncio
async def test_no_send_completes_follow_up_work_and_task_without_outbound(session: Session):
    state = _foundation(session, "follow-up-no-send")
    state.lead.status = LeadStatus.WON
    session.add(state.lead)
    session.commit()
    work_item, _ = _materialize(session, state)

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace, work_item.id
    )

    session.refresh(state.task)
    assert completed.status == WorkItemStatus.COMPLETED
    assert completed.result["action"] == "no_send"
    assert state.task.status == "completed"
    assert session.exec(select(OutboundIntegrationAction)).all() == []
    assert [item["key"] for item in completed.result["agent_skills"]] == [
        "followup_planner"
    ]


@pytest.mark.asyncio
async def test_opt_out_stops_before_draft_or_outbound(session: Session):
    state = _foundation(session, "follow-up-opt-out")
    session.add(
        ConversationMessage(
            lead_id=state.lead.id,
            direction="inbound",
            channel="web",
            content="Don't message me again",
        )
    )
    session.commit()
    work_item, _ = _materialize(session, state)

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace,
        work_item.id,
    )

    assert completed.result["action"] == "no_send"
    assert completed.result["reason"] == "customer_opted_out"
    assert len(completed.result["agent_skills"]) == 1
    assert session.exec(select(OutboundIntegrationAction)).all() == []


@pytest.mark.asyncio
async def test_active_handoff_pauses_before_draft_or_outbound(session: Session):
    state = _foundation(session, "follow-up-active-handoff")
    session.add(
        SalesConversationHandoff(
            workspace_id=state.workspace.id,
            lead_id=state.lead.id,
            reason_code=SalesHandoffReasonCode.HUMAN_REQUESTED,
            explanation="A human owns the conversation",
        )
    )
    session.commit()
    work_item, _ = _materialize(session, state)

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace,
        work_item.id,
    )

    assert completed.result["action"] == "no_send"
    assert completed.result["reason"] == "active_human_handoff"
    assert completed.result["agent_skills"][0]["outcome"] == "human_pause"
    assert session.exec(select(OutboundIntegrationAction)).all() == []


@pytest.mark.asyncio
async def test_newer_customer_reply_invalidates_materialized_follow_up(session: Session):
    state = _foundation(session, "follow-up-newer-reply")
    state.task.created_at = datetime.now(UTC) - timedelta(days=1)
    session.add(state.task)
    session.add(
        ConversationMessage(
            lead_id=state.lead.id,
            direction="inbound",
            channel="web",
            content="I have another question",
        )
    )
    session.commit()
    work_item, _ = _materialize(session, state)

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace,
        work_item.id,
    )

    assert completed.result["action"] == "no_send"
    assert completed.result["reason"] == "newer_customer_reply"
    assert session.exec(select(OutboundIntegrationAction)).all() == []


@pytest.mark.asyncio
async def test_workspace_language_override_drives_generated_draft(session: Session):
    state = _foundation(session, "follow-up-language-override")
    state.workspace.sales_preferred_language = SalesLanguage.FRENCH
    session.add(state.workspace)
    session.commit()
    account = _account(session, state)
    work_item, _ = _materialize(session, state)
    _configure_route(session, work_item, account)

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace,
        work_item.id,
    )

    assert completed.result["message"].startswith("Bonjour")
    assert [item["key"] for item in completed.result["agent_skills"]] == [
        "followup_planner",
        "followup_message_generation",
    ]
    assert completed.result["agent_skills"][1]["result"]["language"] == "french"


@pytest.mark.asyncio
async def test_generator_uses_exact_gateway_attribution_without_direct_send(
    session: Session,
) -> None:
    state = _foundation(session, "follow-up-ai-attribution")
    account = _account(session, state)
    work_item, _ = _materialize(session, state)
    work_item.input = {**work_item.input, "skill_key": "customer_selected_v99"}
    _configure_route(session, work_item, account)
    objective = "Continue the existing conversation about Proposal follow-up"
    evidence_reference = f"follow_up_task.{state.task.id}.reason"
    gateway = Mock()
    gateway.invoke = AsyncMock(
        return_value=Mock(
            content=(
                '{"response_text":"Hi Amina, would you like to continue our discussion?",'
                f'"objective":"{objective}",'
                f'"evidence_references":["{evidence_reference}"],'
                '"language":"english","outcome":"draft_ready",'
                '"escalation_reason":null}'
            )
        )
    )
    settings = _settings().model_copy(update={"llm_mode": "openai_compatible"})

    completed = await SalesWorkItemExecutionService(
        session,
        settings,
        ai_invocation_gateway=gateway,
    ).execute(state.workspace, work_item.id)

    gateway.invoke.assert_awaited_once()
    request = gateway.invoke.await_args.args[0]
    assert request.task_identifier == "sales.followup_message_generation.v1"
    assert request.attribution.work_item_id == work_item.id
    assert completed.result["agent_skills"][0]["key"] == "followup_planner"
    assert completed.result["agent_skills"][1]["key"] == "followup_message_generation"
    assert session.exec(select(OutboundIntegrationAction)).all() == []


@pytest.mark.asyncio
async def test_wrong_follow_up_employee_role_fails_before_running(session: Session):
    state = _foundation(session, "follow-up-wrong-role")
    work_item, _ = _materialize(session, state)
    employee = session.get(AIEmployee, state.follow_assignment.ai_employee_id)
    assert employee is not None
    employee.role_key = AIEmployeeRoleKey.QUALIFICATION
    session.add(employee)
    session.commit()

    with pytest.raises(AgentSkillRoleNotEligibleError):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            state.workspace,
            work_item.id,
        )

    session.refresh(work_item)
    assert work_item.status == WorkItemStatus.ASSIGNED


@pytest.mark.asyncio
async def test_unsafe_configured_draft_is_rejected_and_replaced_safely(session: Session):
    state = _foundation(session, "follow-up-unsafe-draft")
    account = _account(session, state)
    work_item, _ = _materialize(session, state)
    _configure_send(session, work_item, account)
    work_item.input = {
        **work_item.input,
        "message": "Act now for a special discount before the deadline.",
    }
    session.add(work_item)
    session.commit()

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace,
        work_item.id,
    )

    assert "discount" not in completed.result["message"].casefold()
    assert completed.result["agent_skills"][1]["validation_outcome"] == "rejected"
    assert session.exec(select(OutboundIntegrationAction)).all() == []


@pytest.mark.asyncio
async def test_active_task_without_outbound_context_fails_closed(session: Session):
    state = _foundation(session, "follow-up-missing-context")
    work_item, _ = _materialize(session, state)

    with pytest.raises(ValueError, match="outbound context"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            state.workspace, work_item.id
        )

    session.refresh(state.task)
    assert session.get(WorkItem, work_item.id).status == WorkItemStatus.FAILED
    assert state.task.status == "pending"
    assert session.exec(select(OutboundIntegrationAction)).all() == []


@pytest.mark.asyncio
async def test_follow_up_exception_fails_work_item_and_preserves_task(
    session: Session, monkeypatch
):
    state = _foundation(session, "follow-up-error")
    work_item, _ = _materialize(session, state)

    async def fail(self, task, lead, context, contexts):
        raise RuntimeError("follow-up decision failed")

    monkeypatch.setattr(FollowUpAgent, "execute_governed", fail)
    with pytest.raises(RuntimeError, match="decision failed"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            state.workspace, work_item.id
        )

    session.refresh(state.task)
    failed = session.get(WorkItem, work_item.id)
    assert failed.status == WorkItemStatus.FAILED
    assert state.task.status == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("autonomy", "outcome", "send_status", "approvals", "actions", "task_status"),
    [
        (None, CommentTriggerResult.TOOL_ACCESS_DENIED, "created", 0, 0, "pending"),
        (
            AIEmployeeAutonomyLevel.SUGGEST,
            CommentTriggerResult.SUGGESTED,
            "completed",
            0,
            0,
            "pending",
        ),
        (
            AIEmployeeAutonomyLevel.DRAFT_REQUIRES_APPROVAL,
            CommentTriggerResult.APPROVAL_REQUIRED,
            "approval_required",
            1,
            0,
            "pending",
        ),
        (
            AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
            CommentTriggerResult.OUTBOUND_DELIVERED,
            "completed",
            0,
            1,
            "completed",
        ),
        (
            AIEmployeeAutonomyLevel.HIGH_AUTOMATION,
            CommentTriggerResult.OUTBOUND_DELIVERED,
            "completed",
            0,
            1,
            "completed",
        ),
    ],
)
async def test_send_path_reuses_governance_and_outbound(
    session: Session,
    monkeypatch,
    autonomy,
    outcome,
    send_status,
    approvals,
    actions,
    task_status,
):
    state = _foundation(session, f"follow-up-send-{outcome.value}-{autonomy}")
    account = _account(session, state)
    work_item, _ = _materialize(session, state)
    _configure_send(session, work_item, account)
    if autonomy is not None:
        _grant(session, state, account, autonomy)
    _noop_delivery(monkeypatch)

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace, work_item.id
    )

    send_items = session.exec(
        select(WorkItem).where(WorkItem.work_type == "sales_follow_up_message")
    ).all()
    approval_rows = session.exec(select(ApprovalRequest)).all()
    action_rows = session.exec(select(OutboundIntegrationAction)).all()
    session.refresh(state.task)
    assert len(send_items) == 1
    assert send_items[0].status == send_status
    if autonomy is None:
        assert "send_outcome" not in completed.result
        assert completed.result["reason"] == "send_message_assignment_unavailable"
        assert send_items[0].assignment_id is None
    else:
        assert completed.result["send_outcome"] == outcome.value
        assert send_items[0].assignment_id == state.send_assignment.id
    assert len(approval_rows) == approvals
    assert len(action_rows) == actions
    assert state.task.status == task_status
    if action_rows:
        assert action_rows[0].status == OutboundIntegrationActionStatus.DELIVERED
        assert action_rows[0].content == work_item.input["message"]
        assert action_rows[0].payload["work_item_id"] == str(send_items[0].id)


@pytest.mark.asyncio
async def test_missing_send_assignment_creates_one_unresolved_child(session: Session):
    state = _foundation(session, "follow-up-no-send-assignment", send_assignment=False)
    account = _account(session, state)
    work_item, _ = _materialize(session, state)
    _configure_send(session, work_item, account)

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace, work_item.id
    )

    children = session.exec(
        select(WorkItem).where(WorkItem.parent_work_item_id == work_item.id)
    ).all()
    session.refresh(state.task)
    assert len(children) == 1
    assert children[0].status == WorkItemStatus.CREATED
    assert completed.result["reason"] == "send_message_assignment_unavailable"
    assert state.task.status == "pending"


@pytest.mark.asyncio
async def test_cross_workspace_integration_account_is_unroutable_without_delivery(
    session: Session,
):
    state = _foundation(session, "follow-up-wrong-account")
    other = _foundation(session, "follow-up-account-owner")
    wrong_account = _account(session, other)
    work_item, _ = _materialize(session, state)
    _configure_send(session, work_item, wrong_account)

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace, work_item.id
    )

    assert session.exec(select(OutboundIntegrationAction)).all() == []
    child = session.exec(select(WorkItem).where(WorkItem.parent_work_item_id == work_item.id)).one()
    assert child.status == WorkItemStatus.CREATED
    assert child.assignment_id is None
    assert completed.result["reason"] == "send_message_assignment_unavailable"
    session.refresh(state.task)
    assert state.task.status == "pending"


@pytest.mark.asyncio
async def test_provider_failure_keeps_one_action_and_pending_task(session: Session):
    state = _foundation(session, "follow-up-provider-failure")
    account = _account(session, state)
    work_item, _ = _materialize(session, state)
    _configure_send(session, work_item, account)
    _grant(
        session,
        state,
        account,
        AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
    )

    completed = await SalesWorkItemExecutionService(session, _settings()).execute(
        state.workspace, work_item.id
    )

    actions = session.exec(select(OutboundIntegrationAction)).all()
    children = session.exec(
        select(WorkItem).where(WorkItem.parent_work_item_id == work_item.id)
    ).all()
    session.refresh(state.task)
    assert completed.result["send_outcome"] == CommentTriggerResult.OUTBOUND_FAILED
    assert len(actions) == 1
    assert actions[0].status == OutboundIntegrationActionStatus.FAILED
    assert len(children) == 1 and children[0].status == WorkItemStatus.FAILED
    assert state.task.status == "pending"
