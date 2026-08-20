from __future__ import annotations

from sqlmodel import Session, select

from app.core.department_supervisors import (
    DepartmentSupervisorRoutingContext,
    DepartmentSupervisorRoutingDecision,
    DepartmentSupervisorRoutingReason,
)
from app.departments.sales.supervisor.department_supervisor import (
    SalesDepartmentSupervisor,
    SalesEvent,
)
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    Capability,
    Department,
    WorkItem,
)


class SalesWorkItemDepartmentSupervisor:
    """Thin persisted-routing adapter around the existing Sales supervisor."""

    def __init__(
        self,
        session: Session,
        event_supervisor: SalesDepartmentSupervisor | None = None,
    ) -> None:
        self.session = session
        self.event_supervisor = event_supervisor or SalesDepartmentSupervisor()

    def route_event(self, event: SalesEvent) -> str:
        """Preserve the existing deterministic Sales event routing behavior."""

        return self.event_supervisor.route(event)

    def route(
        self,
        context: DepartmentSupervisorRoutingContext,
    ) -> DepartmentSupervisorRoutingDecision:
        work_item = self.session.get(WorkItem, context.work_item_id)
        department = self.session.get(Department, context.department_id)
        if (
            work_item is None
            or department is None
            or work_item.workspace_id != context.workspace_id
            or work_item.department_id != context.department_id
            or work_item.capability_id != context.capability_id
            or department.workspace_id != context.workspace_id
        ):
            return self._unroutable(
                context,
                DepartmentSupervisorRoutingReason.INVALID_CONTEXT,
            )
        if context.capability_id is None:
            return self._unroutable(
                context,
                DepartmentSupervisorRoutingReason.MISSING_CAPABILITY,
            )

        assignment = self.session.exec(
            select(AIEmployeeCapabilityAssignment)
            .join(
                AIEmployee,
                AIEmployeeCapabilityAssignment.ai_employee_id == AIEmployee.id,
            )
            .join(
                Capability,
                AIEmployeeCapabilityAssignment.capability_id == Capability.id,
            )
            .where(
                AIEmployeeCapabilityAssignment.workspace_id == context.workspace_id,
                AIEmployeeCapabilityAssignment.capability_id == context.capability_id,
                AIEmployee.workspace_id == context.workspace_id,
                AIEmployee.department_id == context.department_id,
                AIEmployee.active.is_(True),
                Capability.workspace_id == context.workspace_id,
                Capability.department_id == context.department_id,
                Capability.id == context.capability_id,
                Capability.active.is_(True),
            )
            .order_by(
                AIEmployeeCapabilityAssignment.created_at.asc(),
                AIEmployeeCapabilityAssignment.id.asc(),
            )
        ).first()
        if assignment is None:
            return self._unroutable(
                context,
                DepartmentSupervisorRoutingReason.NO_ELIGIBLE_ASSIGNMENT,
            )
        return DepartmentSupervisorRoutingDecision(
            workspace_id=context.workspace_id,
            department_id=context.department_id,
            work_item_id=context.work_item_id,
            capability_id=context.capability_id,
            assignment_id=assignment.id,
            ai_employee_id=assignment.ai_employee_id,
            routable=True,
        )

    @staticmethod
    def _unroutable(
        context: DepartmentSupervisorRoutingContext,
        reason: DepartmentSupervisorRoutingReason,
    ) -> DepartmentSupervisorRoutingDecision:
        return DepartmentSupervisorRoutingDecision(
            workspace_id=context.workspace_id,
            department_id=context.department_id,
            work_item_id=context.work_item_id,
            capability_id=context.capability_id,
            reason=reason,
        )
