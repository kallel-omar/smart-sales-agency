from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.lead_capture import LeadCaptureSignal
from app.core.work_items import WorkItemStatus
from app.departments.sales.agents.lead_researcher import LeadResearchAgent
from app.departments.sales.agents.qualifier import QualificationAgent
from app.departments.sales.services.acquisition_coordination import (
    SalesAcquisitionCoordinationError,
    SalesAcquisitionWorkItemService,
    SalesWorkItemResultCoordinator,
)
from app.departments.sales.services.work_item_execution import (
    SalesWorkItemExecutionScopeError,
    SalesWorkItemExecutionService,
)
from app.models import (
    AIEmployee,
    Capability,
    Contact,
    Lead,
    LeadResearch,
    LeadStatus,
    WorkItem,
    Workspace,
)
from app.services.capabilities import CapabilityService
from app.services.department_supervisors import DepartmentSupervisorRoutingService
from app.services.departments import DepartmentService
from app.services.lead_capture import LeadCaptureService
from app.services.sales_workforce import SalesWorkforceProvisioningService
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


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="demo",
    )


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = Workspace(slug=slug, name=slug)
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    department = DepartmentService(session).ensure_sales_department(workspace)
    CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.CAPTURE_LEAD,
    )
    return workspace


def _capture(session: Session, workspace: Workspace, *, name: str = "Ada"):
    return LeadCaptureService(session).capture(
        workspace.id,
        LeadCaptureSignal(
            source="api",
            name=name,
            company_name="Acme",
            email=f"{name.casefold()}@example.test",
        ),
    )


def _child(session: Session, parent_id):
    return session.exec(
        select(WorkItem).where(WorkItem.parent_work_item_id == parent_id)
    ).one()


@pytest.mark.asyncio
async def test_persisted_acquisition_chain_is_attributed_linked_and_terminal(
    session: Session,
) -> None:
    workspace = _workspace(session, "acquisition-chain")
    capture_result = _capture(session, workspace)
    capture = session.get(WorkItem, capture_result.work_item_id)
    assert capture is not None
    research = _child(session, capture.id)

    state = await SalesAcquisitionWorkItemService(
        session,
        _settings(),
    ).run(workspace, capture_result.lead_id)
    session.refresh(research)
    qualification = _child(session, research.id)
    items = [capture, research, qualification]
    capabilities = {
        item.id: session.get(Capability, item.capability_id) for item in items
    }
    employees = {
        item.id: session.get(AIEmployee, item.ai_employee_id) for item in items
    }
    persisted_research = session.exec(
        select(LeadResearch).where(LeadResearch.lead_id == capture_result.lead_id)
    ).one()
    lead = session.get(Lead, capture_result.lead_id)

    assert [WorkItemStatus(item.status) for item in items] == [
        WorkItemStatus.COMPLETED,
        WorkItemStatus.COMPLETED,
        WorkItemStatus.COMPLETED,
    ]
    assert [capabilities[item.id].key for item in items] == [  # type: ignore[union-attr]
        BusinessCapabilityKey.CAPTURE_LEAD,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        BusinessCapabilityKey.QUALIFY_LEAD,
    ]
    assert [employees[item.id].role_key for item in items] == [  # type: ignore[union-attr]
        AIEmployeeRoleKey.LEAD_RESEARCH,
        AIEmployeeRoleKey.LEAD_RESEARCH,
        AIEmployeeRoleKey.QUALIFICATION,
    ]
    assert research.parent_work_item_id == capture.id
    assert qualification.parent_work_item_id == research.id
    assert {item.correlation_id for item in items} == {capture.correlation_id}
    assert capture.result == {
        "lead_id": str(capture_result.lead_id),
        "contact_id": str(capture_result.contact_id),
        "customer_id": str(capture_result.customer_id),
        "source": "api",
        "customer_created": True,
        "contact_created": True,
        "lead_created": True,
    }
    assert research.result["lead_research_id"] == str(persisted_research.id)  # type: ignore[index]
    assert qualification.input == {
        "lead_id": str(capture_result.lead_id),
        "lead_research_id": str(persisted_research.id),
    }
    assert qualification.result["outcome"] in {"qualified", "unqualified"}  # type: ignore[index]
    assert state["qualified"] == qualification.result["qualified"]  # type: ignore[index]
    assert state["draft_message"] is None
    assert state["approval_id"] is None
    assert lead is not None and lead.score == qualification.result["score"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_missing_playbook_information_stops_downstream_as_unresolved(
    session: Session,
) -> None:
    workspace = _workspace(session, "acquisition-needs-information")
    workspace.sales_playbook = {
        "schema_version": 1,
        "icp": {"criteria": [], "disqualifiers": []},
        "qualification": {
            "required_information": [
                {
                    "key": "annual_revenue",
                    "description": "Confirmed annual revenue",
                }
            ]
        },
    }
    session.add(workspace)
    session.commit()
    capture_result = _capture(session, workspace)

    state = await SalesAcquisitionWorkItemService(
        session,
        _settings(),
    ).run(workspace, capture_result.lead_id)
    repeated_state = await SalesAcquisitionWorkItemService(
        session,
        _settings(),
    ).run(workspace, capture_result.lead_id)

    lead = session.get(Lead, capture_result.lead_id)
    qualification = session.exec(
        select(WorkItem).where(
            WorkItem.workspace_id == workspace.id,
            WorkItem.work_type == BusinessCapabilityKey.QUALIFY_LEAD.value,
        )
    ).one()
    assert state["qualified"] is False
    assert state["status"] == "needs_more_information"
    assert state["next_action"] == "collect_more_information"
    assert repeated_state["status"] == state["status"]
    assert repeated_state["score"] == state["score"]
    assert lead is not None and lead.status == LeadStatus.RESEARCHED
    assert qualification.result is not None
    assert qualification.result["outcome"] == "needs_more_information"
    assert qualification.result["qualification_policy"]["decision"] == (
        "needs_more_information"
    )
    assert len(session.exec(select(WorkItem)).all()) == 3


@pytest.mark.asyncio
async def test_coordinator_and_workflow_retries_do_not_duplicate_chain(
    session: Session,
) -> None:
    workspace = _workspace(session, "acquisition-idempotent")
    capture_result = _capture(session, workspace)
    capture = session.get(WorkItem, capture_result.work_item_id)
    assert capture is not None
    coordinator = SalesWorkItemResultCoordinator(session)

    first_research = coordinator.process_completed(workspace, capture.id)
    second_research = coordinator.process_completed(workspace, capture.id)
    assert first_research is not None and second_research is not None
    assert first_research.id == second_research.id

    service = SalesAcquisitionWorkItemService(session, _settings())
    first_state = await service.run(workspace, capture_result.lead_id)
    session.refresh(first_research)
    first_qualification = coordinator.process_completed(workspace, first_research.id)
    second_qualification = coordinator.process_completed(workspace, first_research.id)
    second_state = await service.run(workspace, capture_result.lead_id)

    assert first_qualification is not None and second_qualification is not None
    assert first_qualification.id == second_qualification.id
    assert first_state["score"] == second_state["score"]
    assert len(session.exec(select(WorkItem)).all()) == 3
    assert len(session.exec(select(LeadResearch)).all()) == 1


def test_capture_failure_is_terminal_and_creates_no_research_child(
    session: Session,
) -> None:
    workspace = _workspace(session, "acquisition-capture-failure")
    other = _workspace(session, "acquisition-capture-failure-other")
    department = DepartmentService(session).ensure_sales_department(workspace)
    workforce = SalesWorkforceProvisioningService(session).ensure_default_workforce(
        workspace,
        department,
    )
    lead = Lead(
        tenant_id=workspace.slug,
        full_name="Ada",
        company_name="Acme",
        source="api",
    )
    foreign_contact = Contact(workspace_id=other.id, name="Foreign")
    session.add(lead)
    session.add(foreign_contact)
    session.commit()
    session.refresh(lead)
    session.refresh(foreign_contact)
    work_item = WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type="lead_capture",
        title="Capture lead",
        capability=workforce.capabilities[BusinessCapabilityKey.CAPTURE_LEAD],
        input={
            "lead_id": str(lead.id),
            "contact_id": str(foreign_contact.id),
            "source": "api",
        },
    )
    DepartmentSupervisorRoutingService(session).route_and_assign(workspace, work_item.id)

    with pytest.raises(SalesWorkItemExecutionScopeError, match="Contact"):
        SalesWorkItemExecutionService(session, None).execute_capture(
            workspace,
            work_item.id,
        )

    failed = WorkItemService(session).get_work_item(workspace, work_item.id)
    assert failed.status == WorkItemStatus.FAILED
    assert SalesWorkItemResultCoordinator(session).process_completed(
        workspace,
        failed.id,
    ) is None
    assert session.exec(
        select(WorkItem).where(WorkItem.parent_work_item_id == failed.id)
    ).all() == []


@pytest.mark.asyncio
async def test_research_failure_stops_before_qualification(
    session: Session,
    monkeypatch,
) -> None:
    workspace = _workspace(session, "acquisition-research-failure")
    capture_result = _capture(session, workspace)
    capture = session.get(WorkItem, capture_result.work_item_id)
    assert capture is not None
    research = _child(session, capture.id)
    DepartmentSupervisorRoutingService(session).route_and_assign(workspace, research.id)

    async def fail_research(self, lead, **kwargs):
        raise RuntimeError("research failed")

    monkeypatch.setattr(LeadResearchAgent, "run", fail_research)
    with pytest.raises(RuntimeError, match="research failed"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            research.id,
        )

    session.refresh(research)
    assert research.status == WorkItemStatus.FAILED
    assert SalesWorkItemResultCoordinator(session).process_completed(
        workspace,
        research.id,
    ) is None
    assert session.exec(
        select(WorkItem).where(WorkItem.parent_work_item_id == research.id)
    ).all() == []


@pytest.mark.asyncio
async def test_qualification_failure_remains_terminal(
    session: Session,
    monkeypatch,
) -> None:
    workspace = _workspace(session, "acquisition-qualification-failure")
    capture_result = _capture(session, workspace)
    department = DepartmentService(session).ensure_sales_department(workspace)
    SalesWorkforceProvisioningService(session).ensure_default_workforce(
        workspace,
        department,
    )
    capture = session.get(WorkItem, capture_result.work_item_id)
    assert capture is not None
    research = _child(session, capture.id)
    DepartmentSupervisorRoutingService(session).route_and_assign(workspace, research.id)
    await SalesWorkItemExecutionService(session, _settings()).execute(
        workspace,
        research.id,
    )
    qualification = SalesWorkItemResultCoordinator(session).process_completed(
        workspace,
        research.id,
    )
    assert qualification is not None
    DepartmentSupervisorRoutingService(session).route_and_assign(
        workspace,
        qualification.id,
    )

    def fail_qualification(self, lead, research):
        raise RuntimeError("qualification failed")

    monkeypatch.setattr(QualificationAgent, "evaluate", fail_qualification)
    with pytest.raises(RuntimeError, match="qualification failed"):
        await SalesWorkItemExecutionService(session, _settings()).execute(
            workspace,
            qualification.id,
        )

    session.refresh(qualification)
    assert qualification.status == WorkItemStatus.FAILED
    assert SalesWorkItemResultCoordinator(session).process_completed(
        workspace,
        qualification.id,
    ) is None
    assert session.exec(
        select(WorkItem).where(WorkItem.parent_work_item_id == qualification.id)
    ).all() == []


@pytest.mark.asyncio
async def test_coordinator_rejects_cross_workspace_work_item(session: Session) -> None:
    workspace = _workspace(session, "acquisition-scope")
    other = _workspace(session, "acquisition-scope-other")
    capture_result = _capture(session, workspace)

    with pytest.raises(WorkItemNotFoundError, match="not found"):
        SalesWorkItemResultCoordinator(session).process_completed(
            other,
            capture_result.work_item_id,
        )
    with pytest.raises(SalesAcquisitionCoordinationError, match="Lead not found"):
        await SalesAcquisitionWorkItemService(session, _settings()).run(
            other,
            capture_result.lead_id,
        )
