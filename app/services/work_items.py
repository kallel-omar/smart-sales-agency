from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.core.work_items import (
    WorkItemInvalidStateTransitionError,
    WorkItemStateTransitionGuard,
    WorkItemStatus,
)
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    Capability,
    Department,
    WorkItem,
    Workspace,
    utc_now,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentDepartmentMismatchError,
    AIEmployeeCapabilityAssignmentNotFoundError,
    AIEmployeeCapabilityAssignmentScopeError,
)
from app.services.departments import DepartmentNotFoundError
from app.services.workspaces import WorkspaceNotFoundError


class WorkItemNotFoundError(LookupError):
    """Raised when a WorkItem is absent from the requested workspace."""


class WorkItemDepartmentWorkspaceMismatchError(PermissionError):
    """Raised when a Department is not owned by the requested workspace."""


class WorkItemCapabilityScopeError(PermissionError):
    """Raised when a required Capability is outside the WorkItem scope."""


class WorkItemAssignmentRequiredError(ValueError):
    """Raised when a WorkItem state requires an AIEmployee-Capability assignment."""


class WorkItemAssignmentMismatchError(ValueError):
    """Raised when WorkItem assignment fields conflict with the stored assignment."""


class WorkItemRepository:
    """Workspace-scoped WorkItem persistence queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, work_item: WorkItem) -> WorkItem:
        self.session.add(work_item)
        self.session.commit()
        self.session.refresh(work_item)
        return work_item

    def get_for_workspace(self, workspace: Workspace, work_item_id: UUID) -> WorkItem:
        work_item = self.session.exec(
            select(WorkItem).where(
                WorkItem.id == work_item_id,
                WorkItem.workspace_id == workspace.id,
            )
        ).first()
        if work_item is None:
            raise WorkItemNotFoundError("WorkItem not found")
        return work_item

    def list_for_workspace(
        self,
        workspace: Workspace,
        *,
        department: Department | None = None,
        status: WorkItemStatus | None = None,
    ) -> list[WorkItem]:
        statement = select(WorkItem).where(WorkItem.workspace_id == workspace.id)
        if department is not None:
            statement = statement.where(WorkItem.department_id == department.id)
        if status is not None:
            statement = statement.where(WorkItem.status == status)
        statement = statement.order_by(WorkItem.created_at.asc(), WorkItem.id.asc())
        return list(self.session.exec(statement).all())

    def save(self, work_item: WorkItem) -> WorkItem:
        self.session.add(work_item)
        self.session.commit()
        self.session.refresh(work_item)
        return work_item


class WorkItemService:
    """Generic WorkItem lifecycle boundary with strict workspace scoping."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = WorkItemRepository(session)
        self.transition_guard = WorkItemStateTransitionGuard()

    def create_work_item(
        self,
        workspace: Workspace,
        department: Department,
        *,
        work_type: str,
        title: str,
        input: dict,
        capability: Capability | None = None,
        expires_at: datetime | None = None,
    ) -> WorkItem:
        self._require_workspace(workspace)
        stored_department = self._require_department_in_workspace(workspace, department)
        stored_capability = self._require_capability_for_department(
            workspace,
            stored_department,
            capability,
        )
        return self.repository.add(
            WorkItem(
                workspace_id=workspace.id,
                department_id=stored_department.id,
                capability_id=(
                    stored_capability.id if stored_capability is not None else None
                ),
                status=WorkItemStatus.CREATED,
                work_type=self._bounded_text(work_type, "WorkItem type", 100),
                title=self._bounded_text(title, "WorkItem title", 200),
                input=dict(input),
                expires_at=expires_at,
            )
        )

    def get_work_item(self, workspace: Workspace, work_item_id: UUID) -> WorkItem:
        self._require_workspace(workspace)
        return self.repository.get_for_workspace(workspace, work_item_id)

    def list_work_items(
        self,
        workspace: Workspace,
        *,
        department: Department | None = None,
        status: WorkItemStatus | None = None,
    ) -> list[WorkItem]:
        self._require_workspace(workspace)
        if department is not None:
            department = self._require_department_in_workspace(workspace, department)
        canonical_status = WorkItemStatus(status) if status is not None else None
        return self.repository.list_for_workspace(
            workspace,
            department=department,
            status=canonical_status,
        )

    def assign_work_item(
        self,
        workspace: Workspace,
        work_item_id: UUID,
        assignment: AIEmployeeCapabilityAssignment,
    ) -> WorkItem:
        work_item = self.get_work_item(workspace, work_item_id)
        stored_assignment, employee, capability = self._validate_assignment_for_work_item(
            workspace,
            work_item,
            assignment,
        )
        self._require_transition(work_item, WorkItemStatus.ASSIGNED)
        self._apply_assignment(work_item, stored_assignment, employee, capability)
        work_item.status = WorkItemStatus.ASSIGNED
        work_item.updated_at = utc_now()
        return self.repository.save(work_item)

    def transition_work_item(
        self,
        workspace: Workspace,
        work_item_id: UUID,
        status: WorkItemStatus,
        *,
        assignment: AIEmployeeCapabilityAssignment | None = None,
        result: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> WorkItem:
        work_item = self.get_work_item(workspace, work_item_id)
        target_status = WorkItemStatus(status)
        if assignment is not None:
            stored_assignment, employee, capability = self._validate_assignment_for_work_item(
                workspace,
                work_item,
                assignment,
            )
            self._apply_assignment(work_item, stored_assignment, employee, capability)
        current_status = WorkItemStatus(work_item.status)
        if self.transition_guard.is_terminal(current_status):
            self._require_transition(work_item, target_status)
        if target_status in {WorkItemStatus.ASSIGNED, WorkItemStatus.RUNNING}:
            self._require_work_item_assignment(workspace, work_item)
        self._require_transition(work_item, target_status)
        self._apply_transition_data(
            work_item,
            target_status,
            result=result,
            error_code=error_code,
            error_message=error_message,
        )
        return self.repository.save(work_item)

    def _require_transition(
        self,
        work_item: WorkItem,
        target_status: WorkItemStatus,
    ) -> None:
        self.transition_guard.require_transition(
            WorkItemStatus(work_item.status),
            target_status,
        )

    def _apply_transition_data(
        self,
        work_item: WorkItem,
        target_status: WorkItemStatus,
        *,
        result: dict | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        work_item.status = target_status
        now = utc_now()
        if target_status == WorkItemStatus.RUNNING and work_item.started_at is None:
            work_item.started_at = now
        if target_status == WorkItemStatus.COMPLETED:
            work_item.completed_at = now
            if result is not None:
                work_item.result = dict(result)
        if target_status == WorkItemStatus.FAILED:
            if error_code is not None:
                work_item.error_code = self._bounded_text(
                    error_code,
                    "WorkItem error code",
                    100,
                )
            if error_message is not None:
                work_item.error_message = self._bounded_text(
                    error_message,
                    "WorkItem error message",
                    500,
                )
        work_item.updated_at = now

    def _apply_assignment(
        self,
        work_item: WorkItem,
        assignment: AIEmployeeCapabilityAssignment,
        employee: AIEmployee,
        capability: Capability,
    ) -> None:
        self._ensure_existing_assignment_fields_match(work_item, assignment)
        work_item.assignment_id = assignment.id
        work_item.ai_employee_id = employee.id
        work_item.capability_id = capability.id

    def _validate_assignment_for_work_item(
        self,
        workspace: Workspace,
        work_item: WorkItem,
        assignment: AIEmployeeCapabilityAssignment,
    ) -> tuple[AIEmployeeCapabilityAssignment, AIEmployee, Capability]:
        stored_assignment = self.session.get(AIEmployeeCapabilityAssignment, assignment.id)
        if stored_assignment is None:
            raise AIEmployeeCapabilityAssignmentNotFoundError(
                "AIEmployee capability assignment not found"
            )
        if stored_assignment.workspace_id != workspace.id:
            raise AIEmployeeCapabilityAssignmentScopeError(
                "AIEmployee capability assignment does not belong to this workspace"
            )
        employee = self.session.get(AIEmployee, stored_assignment.ai_employee_id)
        capability = self.session.get(Capability, stored_assignment.capability_id)
        if employee is None or capability is None:
            raise AIEmployeeCapabilityAssignmentNotFoundError(
                "AIEmployee capability assignment is incomplete"
            )
        if employee.workspace_id != workspace.id or capability.workspace_id != workspace.id:
            raise AIEmployeeCapabilityAssignmentScopeError(
                "AIEmployee capability assignment does not belong to this workspace"
            )
        if employee.department_id != work_item.department_id:
            raise AIEmployeeCapabilityAssignmentDepartmentMismatchError(
                "AIEmployee must belong to the WorkItem Department"
            )
        if capability.department_id != work_item.department_id:
            raise AIEmployeeCapabilityAssignmentDepartmentMismatchError(
                "Capability must belong to the WorkItem Department"
            )
        if employee.department_id != capability.department_id:
            raise AIEmployeeCapabilityAssignmentDepartmentMismatchError(
                "AIEmployee and Capability must belong to the same Department"
            )
        return stored_assignment, employee, capability

    def _require_work_item_assignment(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> None:
        if work_item.assignment_id is None:
            raise WorkItemAssignmentRequiredError(
                "WorkItem assignment is required for this status"
            )
        assignment = self.session.get(
            AIEmployeeCapabilityAssignment,
            work_item.assignment_id,
        )
        if assignment is None:
            raise WorkItemAssignmentRequiredError(
                "WorkItem assignment is required for this status"
            )
        stored_assignment, employee, capability = self._validate_assignment_for_work_item(
            workspace,
            work_item,
            assignment,
        )
        self._apply_assignment(work_item, stored_assignment, employee, capability)

    @staticmethod
    def _ensure_existing_assignment_fields_match(
        work_item: WorkItem,
        assignment: AIEmployeeCapabilityAssignment,
    ) -> None:
        if work_item.assignment_id is not None and work_item.assignment_id != assignment.id:
            raise WorkItemAssignmentMismatchError(
                "WorkItem assignment does not match the requested assignment"
            )
        if (
            work_item.ai_employee_id is not None
            and work_item.ai_employee_id != assignment.ai_employee_id
        ):
            raise WorkItemAssignmentMismatchError(
                "WorkItem AIEmployee does not match the assignment"
            )
        if (
            work_item.capability_id is not None
            and work_item.capability_id != assignment.capability_id
        ):
            raise WorkItemAssignmentMismatchError(
                "WorkItem Capability does not match the assignment"
            )

    def _require_workspace(self, workspace: Workspace) -> None:
        if self.session.get(Workspace, workspace.id) is None:
            raise WorkspaceNotFoundError("Workspace not found")

    def _require_department_in_workspace(
        self,
        workspace: Workspace,
        department: Department,
    ) -> Department:
        stored = self.session.get(Department, department.id)
        if stored is None:
            raise DepartmentNotFoundError("Department not found")
        if stored.workspace_id != workspace.id:
            raise WorkItemDepartmentWorkspaceMismatchError(
                "Department does not belong to this workspace"
            )
        return stored

    def _require_capability_for_department(
        self,
        workspace: Workspace,
        department: Department,
        capability: Capability | None,
    ) -> Capability | None:
        if capability is None:
            return None
        stored = self.session.get(Capability, capability.id)
        if stored is None:
            raise WorkItemCapabilityScopeError(
                "Capability does not belong to this workspace and Department"
            )
        if (
            stored.workspace_id != workspace.id
            or stored.department_id != department.id
        ):
            raise WorkItemCapabilityScopeError(
                "Capability does not belong to this workspace and Department"
            )
        return stored

    @staticmethod
    def _bounded_text(value: str, label: str, max_length: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{label} is required")
        if len(normalized) > max_length:
            raise ValueError(f"{label} is too long")
        return normalized
