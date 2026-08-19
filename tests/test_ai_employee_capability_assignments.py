from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.services import SalesDepartmentService
from app.departments.sales.supervisor import SalesEvent
from app.models import Department, Workspace
from app.schemas import AIEmployeeCapabilityAssignmentRead
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentDepartmentMismatchError,
    AIEmployeeCapabilityAssignmentNotFoundError,
    AIEmployeeCapabilityAssignmentScopeError,
    AIEmployeeCapabilityAssignmentService,
    DuplicateAIEmployeeCapabilityAssignmentError,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService


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


def _sales_department(session: Session, workspace: Workspace):
    return DepartmentService(session).ensure_sales_department(workspace)


def test_one_employee_can_be_assigned_multiple_capabilities(session: Session) -> None:
    workspace = _workspace(session, "assignment-one-employee")
    department = _sales_department(session, workspace)
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name="Sales Agent A",
    )
    capability_service = CapabilityService(session)
    answer_customer = capability_service.ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    )
    send_message = capability_service.ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.SEND_MESSAGE,
    )
    service = AIEmployeeCapabilityAssignmentService(session)

    first = service.assign(workspace, employee, answer_customer)
    second = service.assign(workspace, employee, send_message)
    read = AIEmployeeCapabilityAssignmentRead.model_validate(first)

    assert read.workspace_id == workspace.id
    assert read.ai_employee_id == employee.id
    assert read.capability_id == answer_customer.id
    assert first.id != second.id
    assert [
        capability.key
        for capability in service.list_capabilities_for_employee(workspace, employee)
    ] == [
        BusinessCapabilityKey.ANSWER_CUSTOMER,
        BusinessCapabilityKey.SEND_MESSAGE,
    ]


def test_one_capability_can_be_assigned_to_multiple_same_role_employees(
    session: Session,
) -> None:
    workspace = _workspace(session, "assignment-one-capability")
    department = _sales_department(session, workspace)
    employee_service = AIEmployeeService(session)
    employee_a = employee_service.create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name="Sales Agent A",
    )
    employee_b = employee_service.create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name="Sales Agent B",
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    )
    service = AIEmployeeCapabilityAssignmentService(session)

    assignment_a = service.assign(workspace, employee_a, capability)
    assignment_b = service.assign(workspace, employee_b, capability)

    assert employee_a.role_key == employee_b.role_key
    assert employee_a.id != employee_b.id
    assert assignment_a.id != assignment_b.id
    assert service.list_capabilities_for_employee(workspace, employee_a) == [capability]
    assert service.list_capabilities_for_employee(workspace, employee_b) == [capability]


def test_duplicate_assignment_is_rejected(session: Session) -> None:
    workspace = _workspace(session, "assignment-duplicate")
    department = _sales_department(session, workspace)
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.LEAD_RESEARCH,
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    service = AIEmployeeCapabilityAssignmentService(session)
    service.assign(workspace, employee, capability)

    with pytest.raises(
        DuplicateAIEmployeeCapabilityAssignmentError,
        match="already assigned",
    ):
        service.assign(workspace, employee, capability)


def test_cross_workspace_assignment_is_rejected(session: Session) -> None:
    workspace_a = _workspace(session, "assignment-workspace-a")
    workspace_b = _workspace(session, "assignment-workspace-b")
    department_a = _sales_department(session, workspace_a)
    department_b = _sales_department(session, workspace_b)
    employee = AIEmployeeService(session).create_for_department(
        workspace_a,
        department_a,
        AIEmployeeRoleKey.QUALIFICATION,
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace_b,
        department_b,
        BusinessCapabilityKey.QUALIFY_LEAD,
    )

    with pytest.raises(
        AIEmployeeCapabilityAssignmentScopeError,
        match="Capability does not belong to this workspace",
    ):
        AIEmployeeCapabilityAssignmentService(session).assign(
            workspace_a,
            employee,
            capability,
        )


def test_cross_department_assignment_is_rejected(session: Session) -> None:
    workspace = _workspace(session, "assignment-department")
    department_service = DepartmentService(session)
    sales_department = department_service.ensure_sales_department(workspace)
    other_department = Department(
        workspace_id=workspace.id,
        kind=DepartmentKind.BUSINESS,
    )
    session.add(other_department)
    session.commit()
    session.refresh(other_department)
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        sales_department,
        AIEmployeeRoleKey.FOLLOW_UP,
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        sales_department,
        BusinessCapabilityKey.FOLLOW_UP_LEAD,
    )
    capability.department_id = other_department.id
    session.add(capability)
    session.commit()
    session.refresh(capability)

    with pytest.raises(
        AIEmployeeCapabilityAssignmentDepartmentMismatchError,
        match="same Department",
    ):
        AIEmployeeCapabilityAssignmentService(session).assign(
            workspace,
            employee,
            capability,
        )


def test_workspace_scoped_reads_do_not_expose_other_workspace_assignments(
    session: Session,
) -> None:
    workspace_a = _workspace(session, "assignment-read-a")
    workspace_b = _workspace(session, "assignment-read-b")
    department_a = _sales_department(session, workspace_a)
    department_b = _sales_department(session, workspace_b)
    employee_a = AIEmployeeService(session).create_for_department(
        workspace_a,
        department_a,
        AIEmployeeRoleKey.SALES_CONVERSATION,
    )
    employee_b = AIEmployeeService(session).create_for_department(
        workspace_b,
        department_b,
        AIEmployeeRoleKey.SALES_CONVERSATION,
    )
    capability_a = CapabilityService(session).ensure_for_department(
        workspace_a,
        department_a,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    )
    capability_b = CapabilityService(session).ensure_for_department(
        workspace_b,
        department_b,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    )
    service = AIEmployeeCapabilityAssignmentService(session)
    service.assign(workspace_a, employee_a, capability_a)
    service.assign(workspace_b, employee_b, capability_b)

    assert service.list_capabilities_for_employee(workspace_a, employee_a) == [
        capability_a
    ]
    with pytest.raises(
        AIEmployeeCapabilityAssignmentScopeError,
        match="AIEmployee does not belong to this workspace",
    ):
        service.list_capabilities_for_employee(workspace_a, employee_b)


def test_assignment_can_be_removed(session: Session) -> None:
    workspace = _workspace(session, "assignment-remove")
    department = _sales_department(session, workspace)
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.LEAD_RESEARCH,
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    service = AIEmployeeCapabilityAssignmentService(session)
    service.assign(workspace, employee, capability)

    service.remove(workspace, employee, capability)

    assert service.list_capabilities_for_employee(workspace, employee) == []
    with pytest.raises(
        AIEmployeeCapabilityAssignmentNotFoundError,
        match="not found",
    ):
        service.remove(workspace, employee, capability)


def test_existing_sales_department_service_remains_compatible(session: Session) -> None:
    workspace = _workspace(session, "assignment-sales-compat")
    department = _sales_department(session, workspace)
    AIEmployeeService(session).ensure_sales_ai_employees(workspace, department)
    CapabilityService(session).ensure_sales_capabilities(workspace, department)
    sales_service = SalesDepartmentService(
        AgentContext(
            settings=object(),
            repository=object(),
            llm=None,
            workspace=workspace,
        )
    )

    assert sales_service.supervisor.route(SalesEvent.NEW_LEAD) == "research_and_qualify"
    assert sales_service.supervisor.route(SalesEvent.INBOUND_MESSAGE) == "sales_conversation"
