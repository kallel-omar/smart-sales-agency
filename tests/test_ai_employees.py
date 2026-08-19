from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.ai_employees import (
    AIEmployeeRoleKey,
    SALES_AI_EMPLOYEE_ROLE_KEYS,
)
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.services import SalesDepartmentService
from app.departments.sales.supervisor import SalesEvent
from app.models import Workspace
from app.schemas import AIEmployeeRead
from app.services.ai_employees import (
    AIEmployeeDepartmentWorkspaceMismatchError,
    AIEmployeeNotFoundError,
    AIEmployeeService,
    UnsupportedAIEmployeeRoleError,
)
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


def test_ai_employee_is_workspace_and_department_scoped(session: Session) -> None:
    workspace = _workspace(session, "employee-a")
    department = DepartmentService(session).ensure_sales_department(workspace)
    service = AIEmployeeService(session)

    employee = service.ensure_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.LEAD_RESEARCH,
    )
    read = AIEmployeeRead.model_validate(employee)

    assert read.workspace_id == workspace.id
    assert read.department_id == department.id
    assert read.role_key is AIEmployeeRoleKey.LEAD_RESEARCH
    assert read.name == "Lead Research"
    assert read.active is True
    assert service.list_for_department(workspace, department) == [employee]


def test_same_ai_employee_role_is_allowed_in_different_workspaces(
    session: Session,
) -> None:
    workspace_a = _workspace(session, "employee-boundary-a")
    workspace_b = _workspace(session, "employee-boundary-b")
    department_service = DepartmentService(session)
    department_a = department_service.ensure_sales_department(workspace_a)
    department_b = department_service.ensure_sales_department(workspace_b)
    service = AIEmployeeService(session)

    employee_a = service.ensure_for_department(
        workspace_a,
        department_a,
        AIEmployeeRoleKey.SALES_CONVERSATION,
    )
    employee_b = service.ensure_for_department(
        workspace_b,
        department_b,
        AIEmployeeRoleKey.SALES_CONVERSATION,
    )

    assert employee_a.id != employee_b.id
    assert employee_a.workspace_id == workspace_a.id
    assert employee_b.workspace_id == workspace_b.id


def test_same_ai_employee_role_is_allowed_in_same_workspace_department(
    session: Session,
) -> None:
    workspace = _workspace(session, "employee-same-role")
    department = DepartmentService(session).ensure_sales_department(workspace)
    service = AIEmployeeService(session)

    employee_a = service.create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name="Sales Agent A",
    )
    employee_b = service.create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name="Sales Agent B",
    )

    assert employee_a.id != employee_b.id
    assert employee_a.role_key == AIEmployeeRoleKey.SALES_CONVERSATION
    assert employee_b.role_key == AIEmployeeRoleKey.SALES_CONVERSATION
    assert [employee.name for employee in service.list_for_department(workspace, department)] == [
        "Sales Agent A",
        "Sales Agent B",
    ]


def test_workspace_cannot_access_another_workspace_ai_employee(
    session: Session,
) -> None:
    workspace_a = _workspace(session, "employee-hidden-a")
    workspace_b = _workspace(session, "employee-hidden-b")
    department = DepartmentService(session).ensure_sales_department(workspace_a)
    service = AIEmployeeService(session)
    employee = service.ensure_for_department(
        workspace_a,
        department,
        AIEmployeeRoleKey.FOLLOW_UP,
    )

    with pytest.raises(AIEmployeeNotFoundError, match="AIEmployee not found"):
        service.get_for_workspace(workspace_b, employee.id)


def test_ai_employee_department_must_belong_to_selected_workspace(
    session: Session,
) -> None:
    workspace_a = _workspace(session, "employee-department-a")
    workspace_b = _workspace(session, "employee-department-b")
    department_a = DepartmentService(session).ensure_sales_department(workspace_a)

    with pytest.raises(
        AIEmployeeDepartmentWorkspaceMismatchError,
        match="Department does not belong to this workspace",
    ):
        AIEmployeeService(session).ensure_for_department(
            workspace_b,
            department_a,
            AIEmployeeRoleKey.QUALIFICATION,
        )


def test_unsupported_ai_employee_role_behavior(
    session: Session,
) -> None:
    workspace = _workspace(session, "employee-constraints")
    department = DepartmentService(session).ensure_sales_department(workspace)
    service = AIEmployeeService(session)

    created = service.create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.LEAD_RESEARCH,
    )

    with pytest.raises(UnsupportedAIEmployeeRoleError, match="not registered"):
        service.ensure_for_department(
            workspace,
            department,
            "not-an-ai-employee-role",  # type: ignore[arg-type]
        )

    assert created.role_key == AIEmployeeRoleKey.LEAD_RESEARCH


def test_current_sales_specialists_can_be_represented(session: Session) -> None:
    workspace = _workspace(session, "employee-sales")
    department = DepartmentService(session).ensure_sales_department(workspace)

    employees = AIEmployeeService(session).ensure_sales_ai_employees(
        workspace,
        department,
    )

    assert [employee.role_key for employee in employees] == list(
        SALES_AI_EMPLOYEE_ROLE_KEYS
    )
    assert {
        employee.role_key: employee.name
        for employee in employees
    } == {
        AIEmployeeRoleKey.LEAD_RESEARCH: "Lead Research",
        AIEmployeeRoleKey.QUALIFICATION: "Qualification",
        AIEmployeeRoleKey.SALES_CONVERSATION: "Sales Conversation",
        AIEmployeeRoleKey.FOLLOW_UP: "Follow-up",
    }


def test_existing_sales_department_service_remains_compatible(session: Session) -> None:
    workspace = _workspace(session, "employee-sales-compat")
    department = DepartmentService(session).ensure_sales_department(workspace)
    AIEmployeeService(session).ensure_sales_ai_employees(workspace, department)
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
