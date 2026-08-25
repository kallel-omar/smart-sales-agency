from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.core.work_items import WorkItemStatus
from app.departments.sales.agents.lead_researcher import LeadResearchAgent
from app.departments.sales.services import (
    M09_SALES_EXECUTION_CAPABILITIES,
    SalesWorkItemExecutionAssignmentError,
    SalesWorkItemExecutionScopeError,
    SalesWorkItemExecutionService,
    SalesWorkItemExecutionStateError,
    SalesWorkItemInputError,
    SalesWorkItemUnsupportedCapabilityError,
)
from app.models import (
    ApprovalRequest,
    ConversationMessage,
    Department,
    Lead,
    LeadResearch,
    Workspace,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService
from app.services.work_items import WorkItemNotFoundError, WorkItemService


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


def _settings(*, require_human_approval: bool = True) -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="demo",
        require_human_approval=require_human_approval,
    )


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = Workspace(slug=slug, name=slug.replace("-", " ").title())
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def _sales_department(session: Session, workspace: Workspace) -> Department:
    return DepartmentService(session).ensure_sales_department(workspace)


def _lead(session: Session, workspace: Workspace) -> Lead:
    lead = Lead(
        tenant_id=workspace.slug,
        full_name="Sarra Ben Ali",
        company_name="Example Co",
        job_title="Sales Director",
        email="sarra@example.com",
        website="https://example.com",
        notes="Needs faster sales follow-up across several channels.",
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def _role_for(capability_key: BusinessCapabilityKey) -> AIEmployeeRoleKey:
    return {
        BusinessCapabilityKey.RESEARCH_COMPANY: AIEmployeeRoleKey.LEAD_RESEARCH,
        BusinessCapabilityKey.QUALIFY_LEAD: AIEmployeeRoleKey.QUALIFICATION,
        BusinessCapabilityKey.ANSWER_CUSTOMER: AIEmployeeRoleKey.SALES_CONVERSATION,
        BusinessCapabilityKey.SEND_MESSAGE: AIEmployeeRoleKey.SALES_CONVERSATION,
    }[capability_key]


def _default_input(capability_key: BusinessCapabilityKey, lead: Lead) -> dict:
    source: dict = {"lead_id": str(lead.id)}
    if capability_key == BusinessCapabilityKey.QUALIFY_LEAD:
        source["research"] = {
            "summary": "Existing research",
            "opportunities": ["Improve response speed"],
        }
    if capability_key == BusinessCapabilityKey.ANSWER_CUSTOMER:
        source.update(
            {
                "channel": "website",
                "customer_message": "What is the price?",
            }
        )
    return source


def _assigned_work_item(
    session: Session,
    capability_key: BusinessCapabilityKey,
    *,
    slug: str,
    source: dict | None = None,
):
    workspace = _workspace(session, slug)
    department = _sales_department(session, workspace)
    lead = _lead(session, workspace)
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        capability_key,
    )
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        _role_for(capability_key),
        name=f"{capability_key.value} specialist",
    )
    assignment = AIEmployeeCapabilityAssignmentService(session).assign(
        workspace,
        employee,
        capability,
    )
    work_item_input = (
        _default_input(capability_key, lead)
        if source is None
        else dict(source)
    )
    if work_item_input.pop("_use_lead_id", False):
        work_item_input["lead_id"] = str(lead.id)
    work_item = WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type=capability_key.value,
        title=f"Execute {capability_key.value}",
        input=work_item_input,
        capability=capability,
    )
    work_item = WorkItemService(session).assign_work_item(
        workspace,
        work_item.id,
        assignment,
    )
    return workspace, department, lead, capability, employee, assignment, work_item


@pytest.mark.asyncio
async def test_research_work_item_executes_existing_agent_and_completes(
    session: Session,
) -> None:
    workspace, _, lead, _, _, _, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        slug="work-item-research",
    )

    completed = await SalesWorkItemExecutionService(
        session,
        _settings(),
    ).execute(workspace, work_item.id)

    research = session.exec(
        select(LeadResearch).where(LeadResearch.lead_id == lead.id)
    ).one()
    assert completed.status == WorkItemStatus.COMPLETED
    assert completed.started_at is not None
    assert completed.completed_at is not None
    assert completed.result == {
        "lead_id": str(lead.id),
        "lead_research_id": str(research.id),
        "summary": research.summary,
        "pain_points": research.pain_points,
        "opportunities": research.opportunities,
        "evidence": research.evidence,
    }


@pytest.mark.asyncio
async def test_qualification_work_item_executes_existing_agent_and_completes(
    session: Session,
) -> None:
    workspace, _, lead, _, _, _, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.QUALIFY_LEAD,
        slug="work-item-qualification",
    )

    completed = await SalesWorkItemExecutionService(
        session,
        _settings(),
    ).execute(workspace, work_item.id)

    session.refresh(lead)
    assert completed.status == WorkItemStatus.COMPLETED
    assert completed.result == {
        "score": 85,
        "qualified": True,
        "outcome": "qualified",
        "reasons": [
            "A direct contact channel is available",
            "A company website is available",
            "The lead role is known",
            "Useful discovery notes are available",
            "The research brief identified relevant opportunities",
        ],
    }
    assert lead.score == 85


@pytest.mark.asyncio
async def test_conversation_work_item_preserves_existing_approval_boundary(
    session: Session,
) -> None:
    workspace, _, lead, _, _, _, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
        slug="work-item-conversation",
    )

    completed = await SalesWorkItemExecutionService(
        session,
        _settings(require_human_approval=True),
    ).execute(workspace, work_item.id)

    approvals = list(
        session.exec(
            select(ApprovalRequest).where(ApprovalRequest.lead_id == lead.id)
        ).all()
    )
    messages = list(
        session.exec(
            select(ConversationMessage).where(ConversationMessage.lead_id == lead.id)
        ).all()
    )
    assert completed.status == WorkItemStatus.COMPLETED
    assert completed.result is not None
    assert completed.result["draft_reply"]
    assert completed.result["detected_stage"] == "qualification"
    assert completed.result["approval_id"] == str(approvals[0].id)
    assert len(approvals) == 1
    assert approvals[0].work_item_id is None
    assert [message.direction for message in messages] == ["inbound"]


def test_m09_capability_to_executor_registry_is_explicit() -> None:
    assert M09_SALES_EXECUTION_CAPABILITIES == {
        BusinessCapabilityKey.RESEARCH_COMPANY,
        BusinessCapabilityKey.QUALIFY_LEAD,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    }


@pytest.mark.asyncio
async def test_created_unassigned_work_item_cannot_execute(session: Session) -> None:
    workspace = _workspace(session, "work-item-unassigned")
    department = _sales_department(session, workspace)
    lead = _lead(session, workspace)
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    work_item = WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type="research_company",
        title="Unassigned research",
        input={"lead_id": str(lead.id)},
        capability=capability,
    )

    with pytest.raises(SalesWorkItemExecutionStateError, match="assigned status"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )


@pytest.mark.asyncio
async def test_assigned_status_without_assignment_cannot_execute(session: Session) -> None:
    workspace = _workspace(session, "work-item-missing-assignment")
    department = _sales_department(session, workspace)
    lead = _lead(session, workspace)
    work_item = WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type="research_company",
        title="Incomplete assignment",
        input={"lead_id": str(lead.id)},
    )
    work_item.status = WorkItemStatus.ASSIGNED
    session.add(work_item)
    session.commit()

    with pytest.raises(SalesWorkItemExecutionAssignmentError, match="complete assignment"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )


@pytest.mark.asyncio
async def test_unsupported_capability_cannot_execute(session: Session) -> None:
    workspace, _, _, _, _, _, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.SEND_MESSAGE,
        slug="work-item-unsupported",
    )

    with pytest.raises(SalesWorkItemUnsupportedCapabilityError, match="not supported"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )
    assert work_item.status == WorkItemStatus.ASSIGNED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability_key", "source", "message"),
    [
        (BusinessCapabilityKey.RESEARCH_COMPANY, {"lead_id": "invalid"}, "valid lead_id"),
        (
            BusinessCapabilityKey.QUALIFY_LEAD,
            {"_use_lead_id": True},
            "requires research",
        ),
        (
            BusinessCapabilityKey.ANSWER_CUSTOMER,
            {"_use_lead_id": True, "channel": "web"},
            "requires customer_message",
        ),
    ],
)
async def test_malformed_input_is_rejected_before_running(
    session: Session,
    capability_key: BusinessCapabilityKey,
    source: dict,
    message: str,
) -> None:
    workspace, _, _, _, _, _, work_item = _assigned_work_item(
        session,
        capability_key,
        slug=f"work-item-input-{capability_key.value}",
        source=source,
    )

    with pytest.raises(SalesWorkItemInputError, match=message):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )
    assert work_item.status == WorkItemStatus.ASSIGNED


@pytest.mark.asyncio
async def test_non_sales_department_cannot_use_sales_executor(session: Session) -> None:
    workspace = _workspace(session, "work-item-non-sales")
    department = Department(
        workspace_id=workspace.id,
        kind=DepartmentKind.BUSINESS,
    )
    session.add(department)
    session.commit()
    session.refresh(department)
    lead = _lead(session, workspace)
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.LEAD_RESEARCH,
        name="Business researcher",
    )
    assignment = AIEmployeeCapabilityAssignmentService(session).assign(
        workspace,
        employee,
        capability,
    )
    work_item = WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type="research_company",
        title="Wrong Department",
        input={"lead_id": str(lead.id)},
        capability=capability,
    )
    WorkItemService(session).assign_work_item(workspace, work_item.id, assignment)

    with pytest.raises(SalesWorkItemExecutionScopeError, match="Sales Department"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )


@pytest.mark.asyncio
async def test_cross_workspace_work_item_is_not_visible(session: Session) -> None:
    workspace_a, _, _, _, _, _, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        slug="work-item-scope-a",
    )
    workspace_b = _workspace(session, "work-item-scope-b")

    with pytest.raises(WorkItemNotFoundError, match="not found"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace_b,
            work_item.id,
        )
    assert workspace_a.id != workspace_b.id


@pytest.mark.asyncio
async def test_cross_workspace_assignment_is_rejected(session: Session) -> None:
    workspace, _, _, _, _, assignment, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        slug="work-item-assignment-scope-a",
    )
    workspace_b = _workspace(session, "work-item-assignment-scope-b")
    assignment.workspace_id = workspace_b.id
    session.add(assignment)
    session.commit()

    with pytest.raises(SalesWorkItemExecutionScopeError, match="this workspace"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )


@pytest.mark.asyncio
async def test_cross_department_assignment_is_rejected(session: Session) -> None:
    workspace, _, _, _, employee, _, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        slug="work-item-assignment-department",
    )
    other_department = Department(
        workspace_id=workspace.id,
        kind=DepartmentKind.BUSINESS,
    )
    session.add(other_department)
    session.commit()
    session.refresh(other_department)
    employee.department_id = other_department.id
    session.add(employee)
    session.commit()

    with pytest.raises(SalesWorkItemExecutionScopeError, match="its Department"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )


@pytest.mark.asyncio
async def test_mismatched_capability_is_rejected(session: Session) -> None:
    workspace, department, _, _, _, _, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        slug="work-item-capability-mismatch",
    )
    other_capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.QUALIFY_LEAD,
    )
    work_item.capability_id = other_capability.id
    session.add(work_item)
    session.commit()

    with pytest.raises(SalesWorkItemExecutionAssignmentError, match="do not match"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        WorkItemStatus.RUNNING,
        WorkItemStatus.COMPLETED,
        WorkItemStatus.FAILED,
        WorkItemStatus.CANCELLED,
        WorkItemStatus.EXPIRED,
    ],
)
async def test_non_assigned_work_item_cannot_reexecute(
    session: Session,
    status: WorkItemStatus,
) -> None:
    workspace, _, _, _, _, _, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        slug=f"work-item-state-{status.value}",
    )
    service = WorkItemService(session)
    if status in {WorkItemStatus.RUNNING, WorkItemStatus.COMPLETED, WorkItemStatus.FAILED}:
        service.transition_work_item(workspace, work_item.id, WorkItemStatus.RUNNING)
    if status != WorkItemStatus.RUNNING:
        service.transition_work_item(workspace, work_item.id, status)

    with pytest.raises(SalesWorkItemExecutionStateError, match="assigned status"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )


@pytest.mark.asyncio
async def test_execution_exception_marks_failed_with_bounded_error_and_reraises(
    session: Session,
    monkeypatch,
) -> None:
    workspace, _, _, _, _, _, work_item = _assigned_work_item(
        session,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        slug="work-item-failure",
    )
    error_message = "provider failure " + ("x" * 600)

    async def fail(self, lead):
        raise RuntimeError(error_message)

    monkeypatch.setattr(LeadResearchAgent, "run", fail)

    with pytest.raises(RuntimeError, match="provider failure"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            work_item.id,
        )

    failed = WorkItemService(session).get_work_item(workspace, work_item.id)
    assert failed.status == WorkItemStatus.FAILED
    assert failed.error_code == "sales_work_item_execution_failed"
    assert failed.error_message == error_message[:500]
    assert len(failed.error_message) == 500
