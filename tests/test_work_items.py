from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.core.work_items import (
    WorkItemInvalidStateTransitionError,
    WorkItemStatus,
)
from app.models import Department, Workspace
from app.schemas import WorkItemRead
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentDepartmentMismatchError,
    AIEmployeeCapabilityAssignmentScopeError,
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService
from app.services.work_items import (
    WorkItemAssignmentRequiredError,
    WorkItemDepartmentWorkspaceMismatchError,
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


def _sales_department(session: Session, workspace: Workspace) -> Department:
    return DepartmentService(session).ensure_sales_department(workspace)


def _business_department(session: Session, workspace: Workspace) -> Department:
    department = Department(
        workspace_id=workspace.id,
        kind=DepartmentKind.BUSINESS,
    )
    session.add(department)
    session.commit()
    session.refresh(department)
    return department


def _work_item(session: Session, workspace: Workspace, department: Department):
    return WorkItemService(session).create_work_item(
        workspace,
        department,
        work_type="generic_sales_task",
        title="Generic sales task",
        input={"lead_id": "lead-1"},
    )


def _assignment(session: Session, workspace: Workspace, department: Department):
    employee = AIEmployeeService(session).create_for_department(
        workspace,
        department,
        AIEmployeeRoleKey.SALES_CONVERSATION,
        name="Sales Agent",
    )
    capability = CapabilityService(session).ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.SEND_MESSAGE,
    )
    return AIEmployeeCapabilityAssignmentService(session).assign(
        workspace,
        employee,
        capability,
    )


def test_created_work_item_with_department_only(session: Session) -> None:
    workspace = _workspace(session, "work-item-create")
    department = _sales_department(session, workspace)

    work_item = _work_item(session, workspace, department)
    read = WorkItemRead.model_validate(work_item)

    assert read.workspace_id == workspace.id
    assert read.department_id == department.id
    assert read.status == WorkItemStatus.CREATED
    assert read.ai_employee_id is None
    assert read.capability_id is None
    assert read.assignment_id is None
    assert read.input == {"lead_id": "lead-1"}
    assert read.result is None


def test_workspace_scoped_reads_and_cross_workspace_isolation(session: Session) -> None:
    workspace_a = _workspace(session, "work-item-read-a")
    workspace_b = _workspace(session, "work-item-read-b")
    department_a = _sales_department(session, workspace_a)
    department_b = _sales_department(session, workspace_b)
    work_item_a = _work_item(session, workspace_a, department_a)
    work_item_b = _work_item(session, workspace_b, department_b)
    service = WorkItemService(session)

    assert service.get_work_item(workspace_a, work_item_a.id) == work_item_a
    assert service.list_work_items(workspace_a) == [work_item_a]
    assert service.list_work_items(workspace_b) == [work_item_b]
    with pytest.raises(WorkItemNotFoundError, match="not found"):
        service.get_work_item(workspace_b, work_item_a.id)


def test_department_workspace_mismatch_is_rejected(session: Session) -> None:
    workspace_a = _workspace(session, "work-item-department-a")
    workspace_b = _workspace(session, "work-item-department-b")
    department_b = _sales_department(session, workspace_b)

    with pytest.raises(
        WorkItemDepartmentWorkspaceMismatchError,
        match="Department does not belong",
    ):
        WorkItemService(session).create_work_item(
            workspace_a,
            department_b,
            work_type="generic",
            title="Wrong department",
            input={},
        )


def test_valid_assignment_transitions_created_to_assigned_and_syncs_fields(
    session: Session,
) -> None:
    workspace = _workspace(session, "work-item-assignment")
    department = _sales_department(session, workspace)
    work_item = _work_item(session, workspace, department)
    assignment = _assignment(session, workspace, department)

    assigned = WorkItemService(session).assign_work_item(
        workspace,
        work_item.id,
        assignment,
    )

    assert assigned.status == WorkItemStatus.ASSIGNED
    assert assigned.assignment_id == assignment.id
    assert assigned.ai_employee_id == assignment.ai_employee_id
    assert assigned.capability_id == assignment.capability_id


def test_cross_workspace_assignment_is_rejected(session: Session) -> None:
    workspace_a = _workspace(session, "work-item-assignment-a")
    workspace_b = _workspace(session, "work-item-assignment-b")
    department_a = _sales_department(session, workspace_a)
    department_b = _sales_department(session, workspace_b)
    work_item = _work_item(session, workspace_a, department_a)
    assignment_b = _assignment(session, workspace_b, department_b)

    with pytest.raises(
        AIEmployeeCapabilityAssignmentScopeError,
        match="does not belong to this workspace",
    ):
        WorkItemService(session).assign_work_item(
            workspace_a,
            work_item.id,
            assignment_b,
        )


def test_cross_department_assignment_is_rejected(session: Session) -> None:
    workspace = _workspace(session, "work-item-cross-department")
    sales_department = _sales_department(session, workspace)
    business_department = _business_department(session, workspace)
    work_item = _work_item(session, workspace, sales_department)
    assignment = _assignment(session, workspace, business_department)

    with pytest.raises(
        AIEmployeeCapabilityAssignmentDepartmentMismatchError,
        match="WorkItem Department",
    ):
        WorkItemService(session).assign_work_item(
            workspace,
            work_item.id,
            assignment,
        )


@pytest.mark.parametrize(
    "target_status",
    [WorkItemStatus.ASSIGNED, WorkItemStatus.RUNNING],
)
def test_assigned_or_running_without_valid_assignment_is_rejected(
    session: Session,
    target_status: WorkItemStatus,
) -> None:
    workspace = _workspace(session, f"work-item-no-assignment-{target_status.value}")
    department = _sales_department(session, workspace)
    work_item = _work_item(session, workspace, department)

    with pytest.raises(WorkItemAssignmentRequiredError, match="assignment is required"):
        WorkItemService(session).transition_work_item(
            workspace,
            work_item.id,
            target_status,
        )


def test_valid_lifecycle_path_and_running_timestamp(session: Session) -> None:
    workspace = _workspace(session, "work-item-lifecycle")
    department = _sales_department(session, workspace)
    work_item = _work_item(session, workspace, department)
    assignment = _assignment(session, workspace, department)
    service = WorkItemService(session)
    work_item = service.assign_work_item(workspace, work_item.id, assignment)

    running = service.transition_work_item(workspace, work_item.id, WorkItemStatus.RUNNING)
    first_started_at = running.started_at
    waiting = service.transition_work_item(workspace, work_item.id, WorkItemStatus.WAITING)
    waiting_status = waiting.status
    running_again = service.transition_work_item(
        workspace,
        work_item.id,
        WorkItemStatus.RUNNING,
    )
    approval_required = service.transition_work_item(
        workspace,
        work_item.id,
        WorkItemStatus.APPROVAL_REQUIRED,
    )
    approval_required_status = approval_required.status
    running_after_approval = service.transition_work_item(
        workspace,
        work_item.id,
        WorkItemStatus.RUNNING,
    )

    assert first_started_at is not None
    assert waiting_status == WorkItemStatus.WAITING
    assert running_again.started_at == first_started_at
    assert approval_required_status == WorkItemStatus.APPROVAL_REQUIRED
    assert running_after_approval.started_at == first_started_at


def test_completed_terminal_sets_completed_at_and_persists_result(
    session: Session,
) -> None:
    workspace = _workspace(session, "work-item-completed")
    department = _sales_department(session, workspace)
    work_item = _work_item(session, workspace, department)
    assignment = _assignment(session, workspace, department)
    service = WorkItemService(session)
    service.assign_work_item(workspace, work_item.id, assignment)
    service.transition_work_item(workspace, work_item.id, WorkItemStatus.RUNNING)

    completed = service.transition_work_item(
        workspace,
        work_item.id,
        WorkItemStatus.COMPLETED,
        result={"outcome": "done"},
    )

    assert completed.status == WorkItemStatus.COMPLETED
    assert completed.result == {"outcome": "done"}
    assert completed.completed_at is not None
    with pytest.raises(WorkItemInvalidStateTransitionError):
        service.transition_work_item(workspace, work_item.id, WorkItemStatus.RUNNING)


def test_failed_terminal_persists_error(session: Session) -> None:
    workspace = _workspace(session, "work-item-failed")
    department = _sales_department(session, workspace)
    work_item = _work_item(session, workspace, department)
    assignment = _assignment(session, workspace, department)
    service = WorkItemService(session)
    service.assign_work_item(workspace, work_item.id, assignment)
    service.transition_work_item(workspace, work_item.id, WorkItemStatus.RUNNING)

    failed = service.transition_work_item(
        workspace,
        work_item.id,
        WorkItemStatus.FAILED,
        error_code="tool_failed",
        error_message="Tool failed safely",
    )

    assert failed.status == WorkItemStatus.FAILED
    assert failed.error_code == "tool_failed"
    assert failed.error_message == "Tool failed safely"
    with pytest.raises(WorkItemInvalidStateTransitionError):
        service.transition_work_item(workspace, work_item.id, WorkItemStatus.RUNNING)


@pytest.mark.parametrize(
    "terminal_status",
    [WorkItemStatus.CANCELLED, WorkItemStatus.EXPIRED],
)
def test_cancelled_and_expired_are_terminal(
    session: Session,
    terminal_status: WorkItemStatus,
) -> None:
    workspace = _workspace(session, f"work-item-terminal-{terminal_status.value}")
    department = _sales_department(session, workspace)
    work_item = _work_item(session, workspace, department)
    service = WorkItemService(session)

    terminal = service.transition_work_item(workspace, work_item.id, terminal_status)

    assert terminal.status == terminal_status
    with pytest.raises(WorkItemInvalidStateTransitionError):
        service.transition_work_item(workspace, work_item.id, WorkItemStatus.ASSIGNED)


def test_invalid_transition_is_rejected(session: Session) -> None:
    workspace = _workspace(session, "work-item-invalid-transition")
    department = _sales_department(session, workspace)
    work_item = _work_item(session, workspace, department)
    assignment = _assignment(session, workspace, department)
    service = WorkItemService(session)
    service.assign_work_item(workspace, work_item.id, assignment)

    with pytest.raises(WorkItemInvalidStateTransitionError):
        service.transition_work_item(workspace, work_item.id, WorkItemStatus.COMPLETED)
