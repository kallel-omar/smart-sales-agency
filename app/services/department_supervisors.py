from __future__ import annotations

from uuid import UUID

from sqlmodel import Session

from app.core.department_supervisors import (
    DepartmentSupervisorNotRegisteredError,
    DepartmentSupervisorRegistry,
    DepartmentSupervisorRoutingContext,
    DepartmentSupervisorRoutingDecision,
    DepartmentSupervisorRoutingReason,
)
from app.core.events import Department as DepartmentKind
from app.departments.sales.supervisor.work_item_adapter import (
    SalesWorkItemDepartmentSupervisor,
)
from app.models import AIEmployeeCapabilityAssignment, Workspace
from app.services.departments import DepartmentService
from app.services.work_items import WorkItemService


def create_department_supervisor_registry(
    session: Session,
) -> DepartmentSupervisorRegistry:
    """Build the currently supported Department Supervisor registrations."""

    registry = DepartmentSupervisorRegistry()
    registry.register(
        DepartmentKind.SALES,
        SalesWorkItemDepartmentSupervisor(session),
    )
    return registry


class DepartmentSupervisorRoutingService:
    """Workspace-safe boundary for selecting and assigning WorkItem targets."""

    def __init__(
        self,
        session: Session,
        registry: DepartmentSupervisorRegistry | None = None,
    ) -> None:
        self.session = session
        self.registry = registry or create_department_supervisor_registry(session)
        self.work_items = WorkItemService(session)
        self.departments = DepartmentService(session)

    def route_work_item(
        self,
        workspace: Workspace,
        work_item_id: UUID,
    ) -> DepartmentSupervisorRoutingDecision:
        work_item = self.work_items.get_work_item(workspace, work_item_id)
        department = self.departments.get_for_workspace(
            workspace,
            work_item.department_id,
        )
        context = DepartmentSupervisorRoutingContext(
            workspace_id=workspace.id,
            department_id=department.id,
            work_item_id=work_item.id,
            capability_id=work_item.capability_id,
        )
        try:
            supervisor = self.registry.resolve(department.kind)
        except DepartmentSupervisorNotRegisteredError:
            return DepartmentSupervisorRoutingDecision(
                workspace_id=context.workspace_id,
                department_id=context.department_id,
                work_item_id=context.work_item_id,
                capability_id=context.capability_id,
                reason=DepartmentSupervisorRoutingReason.UNREGISTERED_DEPARTMENT,
            )
        return supervisor.route(context)

    def route_and_assign(
        self,
        workspace: Workspace,
        work_item_id: UUID,
    ) -> DepartmentSupervisorRoutingDecision:
        decision = self.route_work_item(workspace, work_item_id)
        if not decision.routable or decision.assignment_id is None:
            return decision
        assignment = self.session.get(
            AIEmployeeCapabilityAssignment,
            decision.assignment_id,
        )
        if assignment is None:
            return DepartmentSupervisorRoutingDecision(
                workspace_id=decision.workspace_id,
                department_id=decision.department_id,
                work_item_id=decision.work_item_id,
                capability_id=decision.capability_id,
                reason=DepartmentSupervisorRoutingReason.NO_ELIGIBLE_ASSIGNMENT,
            )
        self.work_items.assign_work_item(workspace, work_item_id, assignment)
        return decision
