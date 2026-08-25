from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from app.core.capabilities import BusinessCapabilityKey
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
    AIEmployeeCapabilityToolAccess,
    Capability,
    Department,
    IntegrationAccount,
    OutboundIntegrationActionType,
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

        candidates = list(
            self.session.exec(
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
            ).all()
        )
        capability = self.session.get(Capability, context.capability_id)
        assignment = self._eligible_assignment(
            context,
            work_item,
            capability,
            candidates,
        )
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

    def _eligible_assignment(
        self,
        context: DepartmentSupervisorRoutingContext,
        work_item: WorkItem,
        capability: Capability | None,
        candidates: list[AIEmployeeCapabilityAssignment],
    ) -> AIEmployeeCapabilityAssignment | None:
        if not candidates or capability is None:
            return None
        if capability.key != BusinessCapabilityKey.SEND_MESSAGE:
            return candidates[0]

        account_value = work_item.input.get("integration_account_id")
        if account_value is None:
            # Preserve generic routing when no concrete external tool target is
            # yet known. Execution remains the authorization boundary.
            return candidates[0]
        try:
            account_id = UUID(str(account_value))
        except (TypeError, ValueError, AttributeError):
            return None
        account = self.session.get(IntegrationAccount, account_id)
        if account is None or account.workspace_id != context.workspace_id or not account.active:
            return None

        candidate_ids = {candidate.id for candidate in candidates}
        eligible_ids = set(
            self.session.exec(
                select(AIEmployeeCapabilityToolAccess.assignment_id).where(
                    AIEmployeeCapabilityToolAccess.workspace_id == context.workspace_id,
                    AIEmployeeCapabilityToolAccess.assignment_id.in_(candidate_ids),
                    AIEmployeeCapabilityToolAccess.integration_account_id == account.id,
                    AIEmployeeCapabilityToolAccess.action_type
                    == OutboundIntegrationActionType.SEND_MESSAGE,
                    AIEmployeeCapabilityToolAccess.active.is_(True),
                )
            ).all()
        )
        return next(
            (candidate for candidate in candidates if candidate.id in eligible_ids),
            None,
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
