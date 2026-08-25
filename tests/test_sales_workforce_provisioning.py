from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    Capability,
    Workspace,
)
from app.services.ai_employees import AIEmployeeService
from app.services.departments import DepartmentService
from app.services.sales_workforce import (
    CANONICAL_SALES_WORKFORCE_CONTRACT,
    SalesWorkforceProvisioningService,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            yield session
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = Workspace(slug=slug, name=slug)
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def test_default_sales_workforce_has_canonical_roles_capabilities_and_assignments(
    session: Session,
) -> None:
    workspace = _workspace(session, "workforce-contract")
    department = DepartmentService(session).ensure_sales_department(workspace)

    result = SalesWorkforceProvisioningService(session).ensure_default_workforce(
        workspace,
        department,
    )

    assert set(result.employees) == set(AIEmployeeRoleKey)
    assert set(result.capabilities) == set(BusinessCapabilityKey)
    assert set(result.assignments) == {
        (role, capability)
        for role, capabilities in CANONICAL_SALES_WORKFORCE_CONTRACT.items()
        for capability in capabilities
    }
    for (role, capability), assignment in result.assignments.items():
        assert assignment.workspace_id == workspace.id
        assert assignment.ai_employee_id == result.employees[role].id
        assert assignment.capability_id == result.capabilities[capability].id


def test_repeated_default_sales_workforce_provisioning_is_idempotent(
    session: Session,
) -> None:
    workspace = _workspace(session, "workforce-idempotent")
    department = DepartmentService(session).ensure_sales_department(workspace)
    service = SalesWorkforceProvisioningService(session)

    first = service.ensure_default_workforce(workspace, department)
    second = service.ensure_default_workforce(workspace, department)

    assert {role: row.id for role, row in first.employees.items()} == {
        role: row.id for role, row in second.employees.items()
    }
    assert {key: row.id for key, row in first.capabilities.items()} == {
        key: row.id for key, row in second.capabilities.items()
    }
    assert {key: row.id for key, row in first.assignments.items()} == {
        key: row.id for key, row in second.assignments.items()
    }
    assert len(session.exec(select(AIEmployee)).all()) == 4
    assert len(session.exec(select(Capability)).all()) == 6
    assert len(session.exec(select(AIEmployeeCapabilityAssignment)).all()) == 6


def test_existing_sales_default_entrypoint_uses_convergent_provisioning(
    session: Session,
) -> None:
    workspace = _workspace(session, "workforce-existing-entrypoint")
    department = DepartmentService(session).ensure_sales_department(workspace)
    service = AIEmployeeService(session)

    first = service.ensure_sales_ai_employees(workspace, department)
    second = service.ensure_sales_ai_employees(workspace, department)

    assert [employee.id for employee in first] == [employee.id for employee in second]
    assert len(session.exec(select(AIEmployee)).all()) == 4
    assert len(session.exec(select(Capability)).all()) == 6
    assert len(session.exec(select(AIEmployeeCapabilityAssignment)).all()) == 6


def test_custom_duplicate_role_employee_remains_supported(session: Session) -> None:
    workspace = _workspace(session, "workforce-custom-capacity")
    department = DepartmentService(session).ensure_sales_department(workspace)
    service = SalesWorkforceProvisioningService(session)
    first = service.ensure_default_workforce(workspace, department)

    custom = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name="Enterprise Sales Conversation",
    )
    second = service.ensure_default_workforce(workspace, department)

    employees = session.exec(select(AIEmployee)).all()
    assert len(employees) == 5
    assert custom in employees
    assert second.employees[AIEmployeeRoleKey.SALES_CONVERSATION].id == (
        first.employees[AIEmployeeRoleKey.SALES_CONVERSATION].id
    )
    assert len(session.exec(select(AIEmployeeCapabilityAssignment)).all()) == 6


def test_custom_employee_is_not_repurposed_as_the_missing_default(session: Session) -> None:
    workspace = _workspace(session, "workforce-custom-first")
    department = DepartmentService(session).ensure_sales_department(workspace)
    custom = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name="Enterprise Sales Conversation",
    )

    result = SalesWorkforceProvisioningService(session).ensure_default_workforce(
        workspace,
        department,
    )

    default_employee = result.employees[AIEmployeeRoleKey.SALES_CONVERSATION]
    custom_assignments = session.exec(
        select(AIEmployeeCapabilityAssignment).where(
            AIEmployeeCapabilityAssignment.ai_employee_id == custom.id
        )
    ).all()
    assert default_employee.id != custom.id
    assert default_employee.name == "Sales Conversation"
    assert custom_assignments == []
    assert len(session.exec(select(AIEmployee)).all()) == 5


def test_default_sales_workforce_is_workspace_isolated(session: Session) -> None:
    workspace_a = _workspace(session, "workforce-scope-a")
    workspace_b = _workspace(session, "workforce-scope-b")
    department_a = DepartmentService(session).ensure_sales_department(workspace_a)
    department_b = DepartmentService(session).ensure_sales_department(workspace_b)
    service = SalesWorkforceProvisioningService(session)

    result_a = service.ensure_default_workforce(workspace_a, department_a)
    result_b = service.ensure_default_workforce(workspace_b, department_b)

    assert {row.id for row in result_a.employees.values()}.isdisjoint(
        {row.id for row in result_b.employees.values()}
    )
    assert all(row.workspace_id == workspace_a.id for row in result_a.assignments.values())
    assert all(row.workspace_id == workspace_b.id for row in result_b.assignments.values())
