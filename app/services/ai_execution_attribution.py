from __future__ import annotations

from uuid import UUID

from sqlmodel import Session

from app.core.ai_execution_attribution import AIExecutionAttribution
from app.models import AIEmployee, Capability, Department, WorkItem, Workspace
from app.services.work_items import WorkItemService


class AIExecutionAttributionNotFoundError(LookupError):
    """Raised when an attributed HIRI entity does not exist."""


class AIExecutionAttributionScopeError(PermissionError):
    """Raised when attributed HIRI entities cross workspace or Department scope."""


class AIExecutionAttributionConflictError(ValueError):
    """Raised when supplied attribution contradicts persisted WorkItem values."""


class AIExecutionAttributionService:
    """Validate and derive provider-independent HIRI execution attribution."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def validate(
        self,
        workspace: Workspace,
        attribution: AIExecutionAttribution | None,
    ) -> AIExecutionAttribution | None:
        if attribution is None:
            return None

        department = self._department(workspace, attribution.department_id)
        employee = self._employee(workspace, attribution.ai_employee_id)
        capability = self._capability(workspace, attribution.capability_id)
        work_item = self._work_item(workspace, attribution.work_item_id)

        if department is not None:
            if employee is not None and employee.department_id != department.id:
                raise AIExecutionAttributionScopeError(
                    "AIEmployee does not belong to the attributed Department"
                )
            if capability is not None and capability.department_id != department.id:
                raise AIExecutionAttributionScopeError(
                    "Capability does not belong to the attributed Department"
                )
        if (
            employee is not None
            and capability is not None
            and employee.department_id != capability.department_id
        ):
            raise AIExecutionAttributionScopeError(
                "AIEmployee and Capability do not belong to the same Department"
            )
        if work_item is not None:
            self._validate_work_item_relationships(
                attribution,
                work_item,
                employee,
                capability,
            )
        return attribution

    def from_work_item(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> AIExecutionAttribution:
        stored = WorkItemService(self.session).get_work_item(workspace, work_item.id)
        attribution = AIExecutionAttribution(
            department_id=stored.department_id,
            ai_employee_id=stored.ai_employee_id,
            capability_id=stored.capability_id,
            work_item_id=stored.id,
        )
        validated = self.validate(workspace, attribution)
        assert validated is not None
        return validated

    def _department(
        self,
        workspace: Workspace,
        department_id: UUID | None,
    ) -> Department | None:
        if department_id is None:
            return None
        department = self.session.get(Department, department_id)
        if department is None:
            raise AIExecutionAttributionNotFoundError("Attributed Department not found")
        if department.workspace_id != workspace.id:
            raise AIExecutionAttributionScopeError(
                "Attributed Department does not belong to this workspace"
            )
        return department

    def _employee(
        self,
        workspace: Workspace,
        employee_id: UUID | None,
    ) -> AIEmployee | None:
        if employee_id is None:
            return None
        employee = self.session.get(AIEmployee, employee_id)
        if employee is None:
            raise AIExecutionAttributionNotFoundError("Attributed AIEmployee not found")
        if employee.workspace_id != workspace.id:
            raise AIExecutionAttributionScopeError(
                "Attributed AIEmployee does not belong to this workspace"
            )
        return employee

    def _capability(
        self,
        workspace: Workspace,
        capability_id: UUID | None,
    ) -> Capability | None:
        if capability_id is None:
            return None
        capability = self.session.get(Capability, capability_id)
        if capability is None:
            raise AIExecutionAttributionNotFoundError("Attributed Capability not found")
        if capability.workspace_id != workspace.id:
            raise AIExecutionAttributionScopeError(
                "Attributed Capability does not belong to this workspace"
            )
        return capability

    def _work_item(
        self,
        workspace: Workspace,
        work_item_id: UUID | None,
    ) -> WorkItem | None:
        if work_item_id is None:
            return None
        work_item = self.session.get(WorkItem, work_item_id)
        if work_item is None:
            raise AIExecutionAttributionNotFoundError("Attributed WorkItem not found")
        if work_item.workspace_id != workspace.id:
            raise AIExecutionAttributionScopeError(
                "Attributed WorkItem does not belong to this workspace"
            )
        return work_item

    @staticmethod
    def _validate_work_item_relationships(
        attribution: AIExecutionAttribution,
        work_item: WorkItem,
        employee: AIEmployee | None,
        capability: Capability | None,
    ) -> None:
        if (
            attribution.department_id is not None
            and attribution.department_id != work_item.department_id
        ):
            raise AIExecutionAttributionConflictError(
                "Attributed Department contradicts the WorkItem"
            )
        if (
            attribution.ai_employee_id is not None
            and work_item.ai_employee_id is not None
            and attribution.ai_employee_id != work_item.ai_employee_id
        ):
            raise AIExecutionAttributionConflictError(
                "Attributed AIEmployee contradicts the WorkItem"
            )
        if (
            attribution.capability_id is not None
            and work_item.capability_id is not None
            and attribution.capability_id != work_item.capability_id
        ):
            raise AIExecutionAttributionConflictError(
                "Attributed Capability contradicts the WorkItem"
            )
        if employee is not None and employee.department_id != work_item.department_id:
            raise AIExecutionAttributionScopeError(
                "Attributed AIEmployee does not belong to the WorkItem Department"
            )
        if capability is not None and capability.department_id != work_item.department_id:
            raise AIExecutionAttributionScopeError(
                "Attributed Capability does not belong to the WorkItem Department"
            )
