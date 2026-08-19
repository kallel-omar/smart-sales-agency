from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    Capability,
    Workspace,
)
from app.services.ai_employees import AIEmployeeNotFoundError
from app.services.capabilities import CapabilityNotFoundError
from app.services.workspaces import WorkspaceNotFoundError


class AIEmployeeCapabilityAssignmentNotFoundError(LookupError):
    """Raised when an assignment is absent from the requested workspace."""


class DuplicateAIEmployeeCapabilityAssignmentError(ValueError):
    """Raised when an employee is already assigned to this capability."""


class AIEmployeeCapabilityAssignmentScopeError(PermissionError):
    """Raised when assignment inputs do not share the requested workspace."""


class AIEmployeeCapabilityAssignmentDepartmentMismatchError(PermissionError):
    """Raised when an employee and capability belong to different Departments."""


class AIEmployeeCapabilityAssignmentRepository:
    """Workspace-scoped AIEmployee-Capability assignment queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(
        self,
        workspace: Workspace,
        employee: AIEmployee,
        capability: Capability,
    ) -> AIEmployeeCapabilityAssignment | None:
        return self.session.exec(
            select(AIEmployeeCapabilityAssignment).where(
                AIEmployeeCapabilityAssignment.workspace_id == workspace.id,
                AIEmployeeCapabilityAssignment.ai_employee_id == employee.id,
                AIEmployeeCapabilityAssignment.capability_id == capability.id,
            )
        ).first()

    def add(
        self,
        assignment: AIEmployeeCapabilityAssignment,
    ) -> AIEmployeeCapabilityAssignment:
        self.session.add(assignment)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateAIEmployeeCapabilityAssignmentError(
                "AIEmployee is already assigned to this capability"
            ) from exc
        self.session.refresh(assignment)
        return assignment

    def list_capabilities_for_employee(
        self,
        workspace: Workspace,
        employee: AIEmployee,
    ) -> list[Capability]:
        statement = (
            select(Capability)
            .join(
                AIEmployeeCapabilityAssignment,
                AIEmployeeCapabilityAssignment.capability_id == Capability.id,
            )
            .where(
                AIEmployeeCapabilityAssignment.workspace_id == workspace.id,
                AIEmployeeCapabilityAssignment.ai_employee_id == employee.id,
                Capability.workspace_id == workspace.id,
            )
            .order_by(
                AIEmployeeCapabilityAssignment.created_at.asc(),
                AIEmployeeCapabilityAssignment.id.asc(),
            )
        )
        return list(self.session.exec(statement).all())

    def delete(self, assignment: AIEmployeeCapabilityAssignment) -> None:
        self.session.delete(assignment)
        self.session.commit()


class AIEmployeeCapabilityAssignmentService:
    """Assign existing AIEmployees to existing Capabilities inside one workspace."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = AIEmployeeCapabilityAssignmentRepository(session)

    def assign(
        self,
        workspace: Workspace,
        employee: AIEmployee,
        capability: Capability,
    ) -> AIEmployeeCapabilityAssignment:
        self._validate_scope(workspace, employee, capability)
        existing = self.repository.get(workspace, employee, capability)
        if existing is not None:
            raise DuplicateAIEmployeeCapabilityAssignmentError(
                "AIEmployee is already assigned to this capability"
            )
        return self.repository.add(
            AIEmployeeCapabilityAssignment(
                workspace_id=workspace.id,
                ai_employee_id=employee.id,
                capability_id=capability.id,
            )
        )

    def list_capabilities_for_employee(
        self,
        workspace: Workspace,
        employee: AIEmployee,
    ) -> list[Capability]:
        self._validate_employee_scope(workspace, employee)
        return self.repository.list_capabilities_for_employee(workspace, employee)

    def remove(
        self,
        workspace: Workspace,
        employee: AIEmployee,
        capability: Capability,
    ) -> None:
        self._validate_scope(workspace, employee, capability)
        assignment = self.repository.get(workspace, employee, capability)
        if assignment is None:
            raise AIEmployeeCapabilityAssignmentNotFoundError(
                "AIEmployee capability assignment not found"
            )
        self.repository.delete(assignment)

    def _validate_scope(
        self,
        workspace: Workspace,
        employee: AIEmployee,
        capability: Capability,
    ) -> None:
        stored_employee = self._validate_employee_scope(workspace, employee)
        stored_capability = self._validate_capability_scope(workspace, capability)
        if stored_employee.department_id != stored_capability.department_id:
            raise AIEmployeeCapabilityAssignmentDepartmentMismatchError(
                "AIEmployee and Capability must belong to the same Department"
            )

    def _validate_employee_scope(
        self,
        workspace: Workspace,
        employee: AIEmployee,
    ) -> AIEmployee:
        self._require_workspace(workspace)
        stored = self.session.get(AIEmployee, employee.id)
        if stored is None:
            raise AIEmployeeNotFoundError("AIEmployee not found")
        if stored.workspace_id != workspace.id:
            raise AIEmployeeCapabilityAssignmentScopeError(
                "AIEmployee does not belong to this workspace"
            )
        return stored

    def _validate_capability_scope(
        self,
        workspace: Workspace,
        capability: Capability,
    ) -> Capability:
        stored = self.session.get(Capability, capability.id)
        if stored is None:
            raise CapabilityNotFoundError("Capability not found")
        if stored.workspace_id != workspace.id:
            raise AIEmployeeCapabilityAssignmentScopeError(
                "Capability does not belong to this workspace"
            )
        return stored

    def _require_workspace(self, workspace: Workspace) -> None:
        if self.session.get(Workspace, workspace.id) is None:
            raise WorkspaceNotFoundError("Workspace not found")
