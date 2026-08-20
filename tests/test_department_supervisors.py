import inspect
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.core.department_supervisors as supervisor_contract
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.department_supervisors import (
    DepartmentSupervisorNotRegisteredError,
    DepartmentSupervisorRegistry,
    DepartmentSupervisorRoutingContext,
    DepartmentSupervisorRoutingReason,
)
from app.core.events import Department as DepartmentKind
from app.core.work_items import WorkItemStatus
from app.departments.sales.supervisor import SalesEvent
from app.departments.sales.supervisor.work_item_adapter import (
    SalesWorkItemDepartmentSupervisor,
)
from app.models import AIEmployeeCapabilityAssignment, Department, Workspace
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.department_supervisors import (
    DepartmentSupervisorRoutingService,
    create_department_supervisor_registry,
)
from app.services.departments import DepartmentService
from app.services.work_items import (
    WorkItemCapabilityScopeError,
    WorkItemNotFoundError,
    WorkItemService,
)


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


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = Workspace(slug=slug, name=slug.replace("-", " ").title())
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def _department(
    session: Session,
    workspace: Workspace,
    kind: DepartmentKind = DepartmentKind.SALES,
) -> Department:
    if kind == DepartmentKind.SALES:
        return DepartmentService(session).ensure_sales_department(workspace)
    department = Department(workspace_id=workspace.id, kind=kind)
    session.add(department)
    session.commit()
    session.refresh(department)
    return department


def _capability(session: Session, workspace: Workspace, department: Department):
    return CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.SEND_MESSAGE,
    )


def _assignment(
    session: Session,
    workspace: Workspace,
    department: Department,
    capability,
    *,
    name: str,
):
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name=name,
    )
    assignment = AIEmployeeCapabilityAssignmentService(session).assign(
        workspace,
        employee,
        capability,
    )
    return employee, assignment


def _work_item(
    session: Session,
    workspace: Workspace,
    department: Department,
    capability=None,
):
    return WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type="send_sales_message",
        title="Send sales message",
        input={"lead_id": "lead-1"},
        capability=capability,
    )


def test_sales_supervisor_resolves_through_generic_registry(session: Session) -> None:
    registry = create_department_supervisor_registry(session)

    supervisor = registry.resolve(DepartmentKind.SALES)

    assert isinstance(supervisor, SalesWorkItemDepartmentSupervisor)
    assert supervisor.route_event(SalesEvent.NEW_LEAD) == "research_and_qualify"


def test_unregistered_department_fails_safely(session: Session) -> None:
    registry = DepartmentSupervisorRegistry()

    with pytest.raises(DepartmentSupervisorNotRegisteredError, match="not registered"):
        registry.resolve(DepartmentKind.MARKETING)


def test_generic_contract_contains_no_sales_assumptions() -> None:
    assert "sales" not in inspect.getsource(supervisor_contract).lower()


def test_valid_work_item_routes_and_assigns_through_work_item_service(
    session: Session,
) -> None:
    workspace = _workspace(session, "supervisor-valid")
    department = _department(session, workspace)
    capability = _capability(session, workspace, department)
    employee, assignment = _assignment(
        session,
        workspace,
        department,
        capability,
        name="Sales Agent",
    )
    work_item = _work_item(session, workspace, department, capability)

    decision = DepartmentSupervisorRoutingService(session).route_and_assign(
        workspace,
        work_item.id,
    )
    assigned = WorkItemService(session).get_work_item(workspace, work_item.id)

    assert decision.routable is True
    assert decision.workspace_id == workspace.id
    assert decision.department_id == department.id
    assert decision.capability_id == capability.id
    assert decision.assignment_id == assignment.id
    assert decision.ai_employee_id == employee.id
    assert assigned.status == WorkItemStatus.ASSIGNED
    assert assigned.assignment_id == assignment.id
    assert assigned.ai_employee_id == employee.id
    assert assigned.capability_id == capability.id


def test_cross_workspace_assignment_is_never_selected(session: Session) -> None:
    workspace_a = _workspace(session, "supervisor-workspace-a")
    workspace_b = _workspace(session, "supervisor-workspace-b")
    department_a = _department(session, workspace_a)
    department_b = _department(session, workspace_b)
    capability_a = _capability(session, workspace_a, department_a)
    capability_b = _capability(session, workspace_b, department_b)
    _assignment(
        session,
        workspace_b,
        department_b,
        capability_b,
        name="Other Workspace Agent",
    )
    work_item = _work_item(session, workspace_a, department_a, capability_a)

    decision = DepartmentSupervisorRoutingService(session).route_work_item(
        workspace_a,
        work_item.id,
    )

    assert decision.routable is False
    assert decision.reason == DepartmentSupervisorRoutingReason.NO_ELIGIBLE_ASSIGNMENT


def test_cross_workspace_work_item_cannot_be_routed(session: Session) -> None:
    workspace_a = _workspace(session, "supervisor-read-a")
    workspace_b = _workspace(session, "supervisor-read-b")
    department_a = _department(session, workspace_a)
    work_item = _work_item(session, workspace_a, department_a)

    with pytest.raises(WorkItemNotFoundError, match="not found"):
        DepartmentSupervisorRoutingService(session).route_work_item(
            workspace_b,
            work_item.id,
        )


def test_supervisor_adapter_rejects_fabricated_cross_workspace_context(
    session: Session,
) -> None:
    workspace_a = _workspace(session, "supervisor-context-a")
    workspace_b = _workspace(session, "supervisor-context-b")
    department_a = _department(session, workspace_a)
    department_b = _department(session, workspace_b)
    capability_a = _capability(session, workspace_a, department_a)
    _assignment(
        session,
        workspace_a,
        department_a,
        capability_a,
        name="Workspace A Agent",
    )
    work_item = _work_item(session, workspace_a, department_a, capability_a)

    decision = SalesWorkItemDepartmentSupervisor(session).route(
        DepartmentSupervisorRoutingContext(
            workspace_id=workspace_b.id,
            department_id=department_b.id,
            work_item_id=work_item.id,
            capability_id=capability_a.id,
        )
    )

    assert decision.routable is False
    assert decision.reason == DepartmentSupervisorRoutingReason.INVALID_CONTEXT


def test_required_capability_must_share_work_item_workspace(session: Session) -> None:
    workspace_a = _workspace(session, "supervisor-capability-workspace-a")
    workspace_b = _workspace(session, "supervisor-capability-workspace-b")
    department_a = _department(session, workspace_a)
    department_b = _department(session, workspace_b)
    capability_b = _capability(session, workspace_b, department_b)

    with pytest.raises(WorkItemCapabilityScopeError, match="workspace and Department"):
        _work_item(session, workspace_a, department_a, capability_b)


def test_required_capability_must_share_work_item_department(session: Session) -> None:
    workspace = _workspace(session, "supervisor-capability-department")
    sales_department = _department(session, workspace)
    other_department = _department(session, workspace, DepartmentKind.BUSINESS)
    capability = _capability(session, workspace, sales_department)

    with pytest.raises(WorkItemCapabilityScopeError, match="workspace and Department"):
        _work_item(session, workspace, other_department, capability)


def test_cross_department_assignment_is_never_selected(session: Session) -> None:
    workspace = _workspace(session, "supervisor-department")
    sales_department = _department(session, workspace)
    other_department = _department(session, workspace, DepartmentKind.BUSINESS)
    capability = _capability(session, workspace, sales_department)
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        other_department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name="Other Department Agent",
    )
    session.add(
        AIEmployeeCapabilityAssignment(
            workspace_id=workspace.id,
            ai_employee_id=employee.id,
            capability_id=capability.id,
        )
    )
    session.commit()
    work_item = _work_item(session, workspace, sales_department, capability)

    decision = DepartmentSupervisorRoutingService(session).route_work_item(
        workspace,
        work_item.id,
    )

    assert decision.routable is False
    assert decision.reason == DepartmentSupervisorRoutingReason.NO_ELIGIBLE_ASSIGNMENT


def test_unrelated_capability_assignment_is_never_selected(session: Session) -> None:
    workspace = _workspace(session, "supervisor-capability")
    department = _department(session, workspace)
    required = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.SEND_MESSAGE,
    )
    unrelated = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    _assignment(
        session,
        workspace,
        department,
        unrelated,
        name="Research Agent",
    )
    work_item = _work_item(session, workspace, department, required)

    decision = DepartmentSupervisorRoutingService(session).route_work_item(
        workspace,
        work_item.id,
    )

    assert decision.routable is False
    assert decision.reason == DepartmentSupervisorRoutingReason.NO_ELIGIBLE_ASSIGNMENT


def test_multiple_eligible_assignments_use_stable_creation_order(session: Session) -> None:
    workspace = _workspace(session, "supervisor-deterministic")
    department = _department(session, workspace)
    capability = _capability(session, workspace, department)
    _, first = _assignment(
        session,
        workspace,
        department,
        capability,
        name="Sales Agent A",
    )
    _, second = _assignment(
        session,
        workspace,
        department,
        capability,
        name="Sales Agent B",
    )
    stable_time = datetime(2026, 1, 1, tzinfo=UTC)
    first.created_at = stable_time
    second.created_at = stable_time
    session.add(first)
    session.add(second)
    session.commit()
    work_item = _work_item(session, workspace, department, capability)

    service = DepartmentSupervisorRoutingService(session)
    first_decision = service.route_work_item(workspace, work_item.id)
    second_decision = service.route_work_item(workspace, work_item.id)

    expected_id = min(first.id, second.id)
    assert first.id != second.id
    assert first_decision.assignment_id == expected_id
    assert second_decision.assignment_id == expected_id


def test_missing_capability_is_not_guessed(session: Session) -> None:
    workspace = _workspace(session, "supervisor-no-capability")
    department = _department(session, workspace)
    capability = _capability(session, workspace, department)
    _assignment(
        session,
        workspace,
        department,
        capability,
        name="Available Agent",
    )
    work_item = _work_item(session, workspace, department)

    decision = DepartmentSupervisorRoutingService(session).route_work_item(
        workspace,
        work_item.id,
    )

    assert decision.routable is False
    assert decision.reason == DepartmentSupervisorRoutingReason.MISSING_CAPABILITY
    assert decision.capability_id is None


def test_unregistered_persisted_department_returns_safe_decision(session: Session) -> None:
    workspace = _workspace(session, "supervisor-unregistered")
    department = _department(session, workspace, DepartmentKind.BUSINESS)
    work_item = _work_item(session, workspace, department)

    decision = DepartmentSupervisorRoutingService(session).route_work_item(
        workspace,
        work_item.id,
    )

    assert decision.routable is False
    assert decision.reason == DepartmentSupervisorRoutingReason.UNREGISTERED_DEPARTMENT
