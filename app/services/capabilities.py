from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.capabilities import (
    BusinessCapabilityKey,
    SALES_BUSINESS_CAPABILITY_KEYS,
    SUPPORTED_BUSINESS_CAPABILITY_KEYS,
)
from app.core.events import Department as DepartmentKind
from app.models import Capability, Department, Workspace
from app.services.departments import DepartmentNotFoundError
from app.services.workspaces import WorkspaceNotFoundError


class CapabilityNotFoundError(LookupError):
    """Raised when a capability is absent from the requested workspace."""


class DuplicateCapabilityError(ValueError):
    """Raised when a Department already has this business capability."""


class UnsupportedCapabilityError(ValueError):
    """Raised when the platform registry does not define this capability key."""


class DepartmentWorkspaceMismatchError(PermissionError):
    """Raised when a Department is not owned by the requested workspace."""


class CapabilityRepository:
    """Workspace-scoped business capability persistence queries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_workspace(
        self,
        workspace: Workspace,
        capability_id: UUID,
    ) -> Capability:
        capability = self.session.exec(
            select(Capability).where(
                Capability.id == capability_id,
                Capability.workspace_id == workspace.id,
            )
        ).first()
        if capability is None:
            raise CapabilityNotFoundError("Capability not found")
        return capability

    def get_by_key(
        self,
        workspace: Workspace,
        department: Department,
        key: BusinessCapabilityKey,
    ) -> Capability | None:
        return self.session.exec(
            select(Capability).where(
                Capability.workspace_id == workspace.id,
                Capability.department_id == department.id,
                Capability.key == key,
            )
        ).first()

    def list_for_department(
        self,
        workspace: Workspace,
        department: Department,
    ) -> list[Capability]:
        statement = (
            select(Capability)
            .where(
                Capability.workspace_id == workspace.id,
                Capability.department_id == department.id,
            )
            .order_by(Capability.created_at.asc(), Capability.id.asc())
        )
        return list(self.session.exec(statement).all())

    def add(self, capability: Capability) -> Capability:
        self.session.add(capability)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateCapabilityError(
                "Department already has this capability"
            ) from exc
        self.session.refresh(capability)
        return capability


class CapabilityService:
    """Small MVP registry for persisted workspace business capabilities."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = CapabilityRepository(session)

    def create_for_department(
        self,
        workspace: Workspace,
        department: Department,
        key: BusinessCapabilityKey,
    ) -> Capability:
        self._require_workspace(workspace)
        self._require_department_in_workspace(workspace, department)
        canonical_key = self._supported_key(key)
        if self.repository.get_by_key(workspace, department, canonical_key) is not None:
            raise DuplicateCapabilityError(
                "Department already has this capability"
            )
        return self.repository.add(
            Capability(
                workspace_id=workspace.id,
                department_id=department.id,
                key=canonical_key,
            )
        )

    def ensure_for_department(
        self,
        workspace: Workspace,
        department: Department,
        key: BusinessCapabilityKey,
    ) -> Capability:
        self._require_workspace(workspace)
        self._require_department_in_workspace(workspace, department)
        canonical_key = self._supported_key(key)
        existing = self.repository.get_by_key(workspace, department, canonical_key)
        if existing is not None:
            return existing
        return self.repository.add(
            Capability(
                workspace_id=workspace.id,
                department_id=department.id,
                key=canonical_key,
            )
        )

    def ensure_sales_capabilities(
        self,
        workspace: Workspace,
        department: Department,
    ) -> list[Capability]:
        self._require_workspace(workspace)
        self._require_department_in_workspace(workspace, department)
        if department.kind != DepartmentKind.SALES:
            raise UnsupportedCapabilityError("Sales capabilities require a Sales department")
        return [
            self.ensure_for_department(workspace, department, key)
            for key in SALES_BUSINESS_CAPABILITY_KEYS
        ]

    def get_for_workspace(
        self,
        workspace: Workspace,
        capability_id: UUID,
    ) -> Capability:
        self._require_workspace(workspace)
        return self.repository.get_for_workspace(workspace, capability_id)

    def list_for_department(
        self,
        workspace: Workspace,
        department: Department,
    ) -> list[Capability]:
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
            raise DepartmentWorkspaceMismatchError(
                "Department does not belong to this workspace"
            )

    @staticmethod
    def _supported_key(key: BusinessCapabilityKey) -> BusinessCapabilityKey:
        try:
            canonical_key = BusinessCapabilityKey(key)
        except (TypeError, ValueError) as exc:
            raise UnsupportedCapabilityError("Capability is not registered") from exc
        if canonical_key not in SUPPORTED_BUSINESS_CAPABILITY_KEYS:
            raise UnsupportedCapabilityError("Capability is not registered")
        return canonical_key
