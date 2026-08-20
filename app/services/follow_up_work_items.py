from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.models import Capability, Department, FollowUpTask, Lead, WorkItem, Workspace
from app.services.department_supervisors import (
    DepartmentSupervisorRoutingDecision,
    DepartmentSupervisorRoutingService,
)
from app.services.work_items import WorkItemService


class FollowUpWorkItemScopeError(PermissionError):
    """Raised when scheduled follow-up ownership crosses workspace scope."""


class FollowUpWorkItemConfigurationError(LookupError):
    """Raised when the Sales Department or follow-up Capability is unavailable."""


class FollowUpWorkItemMaterializationService:
    """Materialize due FollowUpTasks into durable, idempotent HIRI work."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.work_items = WorkItemService(session)
        self.routing = DepartmentSupervisorRoutingService(session)

    def materialize_due(
        self,
        workspace: Workspace,
        follow_up_task_id: UUID,
        *,
        now: datetime | None = None,
    ) -> tuple[WorkItem | None, DepartmentSupervisorRoutingDecision | None]:
        task = self.session.get(FollowUpTask, follow_up_task_id)
        if task is None:
            raise FollowUpWorkItemScopeError("FollowUpTask not found")
        lead = self.session.get(Lead, task.lead_id)
        if lead is None or lead.tenant_id != workspace.slug:
            raise FollowUpWorkItemScopeError("FollowUpTask Lead does not belong to this workspace")

        existing = self._existing(workspace, task.id)
        if existing is not None:
            return existing, None

        due_now = now or datetime.now(UTC)
        due_at = task.due_at
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=UTC)
        if task.status != "pending" or due_at > due_now:
            return None, None

        department, capability = self._sales_follow_up_context(workspace)
        try:
            work_item = self.work_items.create_work_item(
                workspace,
                department,
                work_type="sales_follow_up",
                title="Follow up with lead",
                capability=capability,
                input={
                    "follow_up_task_id": str(task.id),
                    "lead_id": str(lead.id),
                    "reason": task.reason,
                    "scheduled_at": due_at.isoformat(),
                },
                source_follow_up_task_id=task.id,
            )
        except IntegrityError:
            self.session.rollback()
            existing = self._existing(workspace, task.id)
            if existing is None:
                raise
            return existing, None
        decision = self.routing.route_and_assign(workspace, work_item.id)
        return self.work_items.get_work_item(workspace, work_item.id), decision

    def _existing(self, workspace: Workspace, task_id: UUID) -> WorkItem | None:
        return self.session.exec(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace.id,
                WorkItem.source_follow_up_task_id == task_id,
            )
        ).first()

    def _sales_follow_up_context(self, workspace: Workspace) -> tuple[Department, Capability]:
        department = self.session.exec(
            select(Department).where(
                Department.workspace_id == workspace.id,
                Department.kind == DepartmentKind.SALES,
            )
        ).first()
        if department is None:
            raise FollowUpWorkItemConfigurationError(
                "Sales Department is not configured for this workspace"
            )
        capability = self.session.exec(
            select(Capability).where(
                Capability.workspace_id == workspace.id,
                Capability.department_id == department.id,
                Capability.key == BusinessCapabilityKey.FOLLOW_UP_LEAD,
                Capability.active.is_(True),
            )
        ).first()
        if capability is None:
            raise FollowUpWorkItemConfigurationError(
                "follow_up_lead is not configured for the Sales Department"
            )
        return department, capability
