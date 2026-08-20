from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.core.events import Department


class DepartmentSupervisorRoutingReason(StrEnum):
    """Stable reasons why a Department Supervisor could not select a target."""

    INVALID_CONTEXT = "invalid_context"
    MISSING_CAPABILITY = "missing_capability"
    NO_ELIGIBLE_ASSIGNMENT = "no_eligible_assignment"
    UNREGISTERED_DEPARTMENT = "unregistered_department"


@dataclass(slots=True, frozen=True)
class DepartmentSupervisorRoutingContext:
    """Workspace-scoped facts a supervisor may use to route one WorkItem."""

    workspace_id: UUID
    department_id: UUID
    work_item_id: UUID
    capability_id: UUID | None


@dataclass(slots=True, frozen=True)
class DepartmentSupervisorRoutingDecision:
    """Result of selecting, or declining to select, a WorkItem target."""

    workspace_id: UUID
    department_id: UUID
    work_item_id: UUID
    capability_id: UUID | None
    assignment_id: UUID | None = None
    ai_employee_id: UUID | None = None
    routable: bool = False
    reason: DepartmentSupervisorRoutingReason | None = None


class DepartmentSupervisor(Protocol):
    """Non-executing contract for selecting a WorkItem assignment target."""

    def route(
        self,
        context: DepartmentSupervisorRoutingContext,
    ) -> DepartmentSupervisorRoutingDecision: ...


class DepartmentSupervisorNotRegisteredError(LookupError):
    """Raised when no supervisor is registered for a Department kind."""


class DepartmentSupervisorRegistry:
    """Resolve orchestration supervisors by the platform Department contract."""

    def __init__(self) -> None:
        self._supervisors: dict[Department, DepartmentSupervisor] = {}

    def register(
        self,
        department_kind: Department,
        supervisor: DepartmentSupervisor,
    ) -> None:
        self._supervisors[Department(department_kind)] = supervisor

    def resolve(self, department_kind: Department) -> DepartmentSupervisor:
        try:
            canonical_kind = Department(department_kind)
        except (TypeError, ValueError) as exc:
            raise DepartmentSupervisorNotRegisteredError(
                "Department Supervisor is not registered"
            ) from exc
        supervisor = self._supervisors.get(canonical_kind)
        if supervisor is None:
            raise DepartmentSupervisorNotRegisteredError(
                "Department Supervisor is not registered"
            )
        return supervisor
