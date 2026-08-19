from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from app.core.ai_employees import (
    AI_EMPLOYEE_ROLE_DEFAULT_NAMES,
    AIEmployeeRoleKey,
    SALES_AI_EMPLOYEE_ROLE_KEYS,
    SUPPORTED_AI_EMPLOYEE_ROLE_KEYS,
)
from app.core.events import Department as DepartmentKind
from app.models import AIEmployee, Department, Workspace
from app.services.departments import DepartmentNotFoundError
from app.services.workspaces import WorkspaceNotFoundError


class AIEmployeeNotFoundError(LookupError):
    """Raised when an AIEmployee is absent from the requested workspace."""


class UnsupportedAIEmployeeRoleError(ValueError):
    """Raised when the platform registry does not define this AIEmployee role."""


class AIEmployeeDepartmentWorkspaceMismatchError(PermissionError):
    """Raised when a Department is not owned by the requested workspace."""


class AIEmployeeRepository:
    """Workspace-scoped AIEmployee persistence queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_workspace(
        self,
        workspace: Workspace,
        employee_id: UUID,
    ) -> AIEmployee:
        employee = self.session.exec(
            select(AIEmployee).where(
                AIEmployee.id == employee_id,
                AIEmployee.workspace_id == workspace.id,
            )
        ).first()
        if employee is None:
            raise AIEmployeeNotFoundError("AIEmployee not found")
        return employee

    def list_for_department(
        self,
        workspace: Workspace,
        department: Department,
    ) -> list[AIEmployee]:
        statement = (
            select(AIEmployee)
            .where(
                AIEmployee.workspace_id == workspace.id,
                AIEmployee.department_id == department.id,
            )
            .order_by(AIEmployee.created_at.asc(), AIEmployee.id.asc())
        )
        return list(self.session.exec(statement).all())

    def add(self, employee: AIEmployee) -> AIEmployee:
        self.session.add(employee)
        self.session.commit()
        self.session.refresh(employee)
        return employee


class AIEmployeeService:
    """Small MVP registry for persisted workspace AIEmployee specialists."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = AIEmployeeRepository(session)

    def create_for_department(
        self,
        workspace: Workspace,
        department: Department,
        role_key: AIEmployeeRoleKey,
        *,
        name: str | None = None,
    ) -> AIEmployee:
        self._require_workspace(workspace)
        self._require_department_in_workspace(workspace, department)
        canonical_role = self._supported_role(role_key)
        return self.repository.add(
            AIEmployee(
                workspace_id=workspace.id,
                department_id=department.id,
                role_key=canonical_role,
                name=self._name(canonical_role, name),
            )
        )

    def ensure_for_department(
        self,
        workspace: Workspace,
        department: Department,
        role_key: AIEmployeeRoleKey,
        *,
        name: str | None = None,
    ) -> AIEmployee:
        self._require_workspace(workspace)
        self._require_department_in_workspace(workspace, department)
        canonical_role = self._supported_role(role_key)
        return self.repository.add(
            AIEmployee(
                workspace_id=workspace.id,
                department_id=department.id,
                role_key=canonical_role,
                name=self._name(canonical_role, name),
            )
        )

    def ensure_sales_ai_employees(
        self,
        workspace: Workspace,
        department: Department,
    ) -> list[AIEmployee]:
        self._require_workspace(workspace)
        self._require_department_in_workspace(workspace, department)
        if department.kind != DepartmentKind.SALES:
            raise UnsupportedAIEmployeeRoleError("Sales AIEmployees require a Sales department")
        return [
            self.ensure_for_department(workspace, department, role_key)
            for role_key in SALES_AI_EMPLOYEE_ROLE_KEYS
        ]

    def get_for_workspace(
        self,
        workspace: Workspace,
        employee_id: UUID,
    ) -> AIEmployee:
        self._require_workspace(workspace)
        return self.repository.get_for_workspace(workspace, employee_id)

    def list_for_department(
        self,
        workspace: Workspace,
        department: Department,
    ) -> list[AIEmployee]:
        self._require_workspace(workspace)
        self._require_department_in_workspace(workspace, department)
        return self.repository.list_for_department(workspace, department)

    def _require_workspace(self, workspace: Workspace) -> None:
        if self.session.get(Workspace, workspace.id) is None:
            raise WorkspaceNotFoundError("Workspace not found")

    def _require_department_in_workspace(
        self,
        workspace: Workspace,
        department: Department,
    ) -> None:
        stored = self.session.get(Department, department.id)
        if stored is None:
            raise DepartmentNotFoundError("Department not found")
        if stored.workspace_id != workspace.id:
            raise AIEmployeeDepartmentWorkspaceMismatchError(
                "Department does not belong to this workspace"
            )

    @staticmethod
    def _supported_role(role_key: AIEmployeeRoleKey) -> AIEmployeeRoleKey:
        try:
            canonical_role = AIEmployeeRoleKey(role_key)
        except (TypeError, ValueError) as exc:
            raise UnsupportedAIEmployeeRoleError("AIEmployee role is not registered") from exc
        if canonical_role not in SUPPORTED_AI_EMPLOYEE_ROLE_KEYS:
            raise UnsupportedAIEmployeeRoleError("AIEmployee role is not registered")
        return canonical_role

    @staticmethod
    def _name(role_key: AIEmployeeRoleKey, name: str | None) -> str:
        if name is None:
            return AI_EMPLOYEE_ROLE_DEFAULT_NAMES[role_key]
        normalized = name.strip()
        if not normalized:
            return AI_EMPLOYEE_ROLE_DEFAULT_NAMES[role_key]
        return normalized
