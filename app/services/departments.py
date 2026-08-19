from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.events import Department as DepartmentKind
from app.models import Department, Workspace
from app.services.workspaces import WorkspaceNotFoundError

MVP_DEPARTMENT_KINDS = frozenset({DepartmentKind.SALES})


class DepartmentNotFoundError(LookupError):
    """Raised when a department is absent from the requested workspace."""


class DuplicateDepartmentError(ValueError):
    """Raised when a workspace already has a department of this kind."""


class UnsupportedDepartmentError(ValueError):
    """Raised when the MVP has no persisted registration for the requested kind."""


class DepartmentRepository:
    """Workspace-scoped Department persistence queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_workspace(
        self,
        workspace: Workspace,
        department_id: UUID,
    ) -> Department:
        department = self.session.exec(
            select(Department).where(
                Department.id == department_id,
                Department.workspace_id == workspace.id,
            )
        ).first()
        if department is None:
            raise DepartmentNotFoundError("Department not found")
        return department

    def get_by_kind(
        self,
        workspace: Workspace,
        kind: DepartmentKind,
    ) -> Department | None:
        return self.session.exec(
            select(Department).where(
                Department.workspace_id == workspace.id,
                Department.kind == kind,
            )
        ).first()

    def list_for_workspace(self, workspace: Workspace) -> list[Department]:
        statement = (
            select(Department)
            .where(Department.workspace_id == workspace.id)
            .order_by(Department.created_at.asc(), Department.id.asc())
        )
        return list(self.session.exec(statement).all())

    def add(self, department: Department) -> Department:
        self.session.add(department)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateDepartmentError(
                "Workspace already has this department"
            ) from exc
        self.session.refresh(department)
        return department


class DepartmentService:
    """Small MVP registry for persisted workspace Departments."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = DepartmentRepository(session)

    def create_for_workspace(
        self,
        workspace: Workspace,
        kind: DepartmentKind,
    ) -> Department:
        self._require_workspace(workspace)
        canonical_kind = self._supported_kind(kind)
        if self.repository.get_by_kind(workspace, canonical_kind) is not None:
            raise DuplicateDepartmentError(
                "Workspace already has this department"
            )
        return self.repository.add(
            Department(
                workspace_id=workspace.id,
                kind=canonical_kind,
            )
        )

    def ensure_sales_department(self, workspace: Workspace) -> Department:
        return self.ensure_for_workspace(workspace, DepartmentKind.SALES)

    def ensure_for_workspace(
        self,
        workspace: Workspace,
        kind: DepartmentKind,
    ) -> Department:
        self._require_workspace(workspace)
        canonical_kind = self._supported_kind(kind)
        existing = self.repository.get_by_kind(workspace, canonical_kind)
        if existing is not None:
            return existing
        return self.repository.add(
            Department(
                workspace_id=workspace.id,
                kind=canonical_kind,
            )
        )

    def get_for_workspace(
        self,
        workspace: Workspace,
        department_id: UUID,
    ) -> Department:
        self._require_workspace(workspace)
        return self.repository.get_for_workspace(workspace, department_id)

    def list_for_workspace(self, workspace: Workspace) -> list[Department]:
        self._require_workspace(workspace)
        return self.repository.list_for_workspace(workspace)

    def _require_workspace(self, workspace: Workspace) -> None:
        if self.session.get(Workspace, workspace.id) is None:
            raise WorkspaceNotFoundError("Workspace not found")

    @staticmethod
    def _supported_kind(kind: DepartmentKind) -> DepartmentKind:
        try:
            canonical_kind = DepartmentKind(kind)
        except (TypeError, ValueError) as exc:
            raise UnsupportedDepartmentError("Department is not registered") from exc
        if canonical_kind not in MVP_DEPARTMENT_KINDS:
            raise UnsupportedDepartmentError("Department is not registered")
        return canonical_kind
