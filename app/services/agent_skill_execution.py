"""Persisted authorization resolver for future AgentSkill execution."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from app.core.agent_skill_execution import AgentSkillExecutionContext
from app.core.agent_skills import AgentSkillRegistry, effective_agent_skill_tools
from app.core.events import Department as DepartmentKind
from app.core.work_items import WorkItemStatus
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    AIEmployeeCapabilityToolAccess,
    Capability,
    Department,
    WorkItem,
    Workspace,
)
from app.services.work_items import WorkItemService


class AgentSkillExecutionAuthorizationError(PermissionError):
    """Raised when persisted HIRI authority does not permit skill context creation."""


class AgentSkillExecutionStateError(ValueError):
    """Raised when a WorkItem is not at the existing pre-execution boundary."""


class AgentSkillExecutionContextResolver:
    """Build immutable skill identity only after persisted authorization checks."""

    def __init__(self, session: Session, registry: AgentSkillRegistry) -> None:
        self.session = session
        self.registry = registry
        self.work_items = WorkItemService(session)

    def resolve(
        self,
        workspace: Workspace,
        work_item_id: UUID,
        *,
        skill_key: str,
        skill_version: str,
    ) -> AgentSkillExecutionContext:
        definition = self.registry.resolve(skill_key, skill_version)
        work_item = self.work_items.get_work_item(workspace, work_item_id)
        if WorkItemStatus(work_item.status) is not WorkItemStatus.ASSIGNED:
            raise AgentSkillExecutionStateError(
                "AgentSkill context requires an assigned WorkItem"
            )
        department = self._department(workspace, work_item, definition.department)
        assignment, employee, capability = self._assignment_chain(workspace, work_item)
        definition = self.registry.require_eligible(
            skill_key,
            skill_version,
            department=department.kind,
            role=employee.role_key,
            capability=capability.key,
        )
        authorized_tools = self._active_granted_actions(workspace, assignment)
        effective_tools = effective_agent_skill_tools(authorized_tools, definition)
        return AgentSkillExecutionContext(
            workspace_id=workspace.id,
            department_id=department.id,
            department=department.kind,
            work_item_id=work_item.id,
            ai_employee_id=employee.id,
            employee_role=employee.role_key,
            assignment_id=assignment.id,
            capability_id=capability.id,
            capability=capability.key,
            skill_key=definition.key,
            skill_version=definition.version,
            input_contract=definition.input_contract,
            output_contract=definition.output_contract,
            validator=definition.validator,
            instruction_component=definition.instruction_component,
            effective_tool_ceiling=effective_tools,
            attribution_identifier=definition.attribution_identifier,
        )

    def _department(
        self,
        workspace: Workspace,
        work_item: WorkItem,
        expected_kind: DepartmentKind,
    ) -> Department:
        department = self.session.get(Department, work_item.department_id)
        if (
            department is None
            or department.workspace_id != workspace.id
            or department.kind != expected_kind
        ):
            raise AgentSkillExecutionAuthorizationError(
                "WorkItem Department is not authorized for this AgentSkill"
            )
        return department

    def _assignment_chain(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> tuple[AIEmployeeCapabilityAssignment, AIEmployee, Capability]:
        if (
            work_item.assignment_id is None
            or work_item.ai_employee_id is None
            or work_item.capability_id is None
        ):
            raise AgentSkillExecutionAuthorizationError(
                "WorkItem has no persisted AgentSkill authorization assignment"
            )
        assignment = self.session.get(
            AIEmployeeCapabilityAssignment,
            work_item.assignment_id,
        )
        employee = self.session.get(AIEmployee, work_item.ai_employee_id)
        capability = self.session.get(Capability, work_item.capability_id)
        if assignment is None or employee is None or capability is None:
            raise AgentSkillExecutionAuthorizationError(
                "WorkItem AgentSkill authorization assignment is incomplete"
            )
        if (
            assignment.workspace_id != workspace.id
            or employee.workspace_id != workspace.id
            or capability.workspace_id != workspace.id
            or employee.department_id != work_item.department_id
            or capability.department_id != work_item.department_id
            or assignment.ai_employee_id != employee.id
            or assignment.capability_id != capability.id
            or not employee.active
            or not capability.active
        ):
            raise AgentSkillExecutionAuthorizationError(
                "WorkItem AgentSkill authorization assignment is invalid"
            )
        return assignment, employee, capability

    def _active_granted_actions(
        self,
        workspace: Workspace,
        assignment: AIEmployeeCapabilityAssignment,
    ) -> frozenset[str]:
        grants = self.session.exec(
            select(AIEmployeeCapabilityToolAccess).where(
                AIEmployeeCapabilityToolAccess.workspace_id == workspace.id,
                AIEmployeeCapabilityToolAccess.assignment_id == assignment.id,
                AIEmployeeCapabilityToolAccess.active.is_(True),
            )
        ).all()
        return frozenset(str(grant.action_type) for grant in grants)
