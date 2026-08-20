from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.config import Settings
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.core.work_items import WorkItemStatus
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.agents.lead_researcher import LeadResearchAgent
from app.departments.sales.agents.qualifier import QualificationAgent
from app.departments.sales.services.conversation_turn_service import (
    SalesConversationTurnInput,
    SalesConversationTurnService,
)
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    Capability,
    Department,
    Lead,
    WorkItem,
    Workspace,
)
from app.services.ai_execution_attribution import AIExecutionAttributionService
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.repository import NotFoundError, SalesRepository
from app.services.work_items import WorkItemService

M09_SALES_EXECUTION_CAPABILITIES = frozenset(
    {
        BusinessCapabilityKey.RESEARCH_COMPANY,
        BusinessCapabilityKey.QUALIFY_LEAD,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    }
)


class SalesWorkItemExecutionStateError(ValueError):
    """Raised when a WorkItem is not ready for one-time Sales execution."""


class SalesWorkItemExecutionAssignmentError(ValueError):
    """Raised when the persisted WorkItem assignment chain is invalid."""


class SalesWorkItemExecutionScopeError(PermissionError):
    """Raised when Sales execution inputs cross workspace or Department scope."""


class SalesWorkItemUnsupportedCapabilityError(ValueError):
    """Raised when no M09 Sales executor exists for the assigned Capability."""


class SalesWorkItemInputError(ValueError):
    """Raised when WorkItem input cannot drive the existing Sales execution path."""


class SalesWorkItemResultError(ValueError):
    """Raised when an existing Sales path returns a non-JSON-safe result."""


@dataclass(slots=True, frozen=True)
class _ExecutionTarget:
    work_item: WorkItem
    assignment: AIEmployeeCapabilityAssignment
    employee: AIEmployee
    capability: Capability
    capability_key: BusinessCapabilityKey


SalesExecutor = Callable[[Workspace, WorkItem], Awaitable[dict[str, Any]]]


class SalesWorkItemExecutionService:
    """Run assigned Sales WorkItems through existing specialist boundaries."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        ai_invocation_gateway: AIInvocationGateway | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.repository = SalesRepository(session)
        self.work_items = WorkItemService(session)
        self.ai_invocation_gateway = ai_invocation_gateway
        self.attribution = AIExecutionAttributionService(session)

    async def execute(
        self,
        workspace: Workspace,
        work_item_id: UUID,
    ) -> WorkItem:
        target = self._execution_target(workspace, work_item_id)
        executor = self._executors()[target.capability_key]
        self._validate_input(workspace, target.work_item, target.capability_key)
        self.work_items.transition_work_item(
            workspace,
            work_item_id,
            WorkItemStatus.RUNNING,
        )
        try:
            result = self._json_safe_result(
                await executor(workspace, target.work_item)
            )
        except Exception as exc:
            self.work_items.transition_work_item(
                workspace,
                work_item_id,
                WorkItemStatus.FAILED,
                error_code="sales_work_item_execution_failed",
                error_message=self._bounded_error_message(exc),
            )
            raise
        return self.work_items.transition_work_item(
            workspace,
            work_item_id,
            WorkItemStatus.COMPLETED,
            result=result,
        )

    def _executors(self) -> dict[BusinessCapabilityKey, SalesExecutor]:
        return {
            BusinessCapabilityKey.RESEARCH_COMPANY: self._execute_research,
            BusinessCapabilityKey.QUALIFY_LEAD: self._execute_qualification,
            BusinessCapabilityKey.ANSWER_CUSTOMER: self._execute_conversation,
        }

    def _execution_target(
        self,
        workspace: Workspace,
        work_item_id: UUID,
    ) -> _ExecutionTarget:
        work_item = self.work_items.get_work_item(workspace, work_item_id)
        if WorkItemStatus(work_item.status) != WorkItemStatus.ASSIGNED:
            raise SalesWorkItemExecutionStateError(
                "Sales WorkItem execution requires assigned status"
            )
        department = self.session.get(Department, work_item.department_id)
        if department is None or department.workspace_id != workspace.id:
            raise SalesWorkItemExecutionScopeError(
                "WorkItem Department does not belong to this workspace"
            )
        if department.kind != DepartmentKind.SALES:
            raise SalesWorkItemExecutionScopeError(
                "Sales WorkItem execution requires a Sales Department"
            )
        if (
            work_item.assignment_id is None
            or work_item.ai_employee_id is None
            or work_item.capability_id is None
        ):
            raise SalesWorkItemExecutionAssignmentError(
                "Sales WorkItem requires a complete assignment"
            )
        assignment = self.session.get(
            AIEmployeeCapabilityAssignment,
            work_item.assignment_id,
        )
        if assignment is None:
            raise SalesWorkItemExecutionAssignmentError(
                "Sales WorkItem assignment was not found"
            )
        employee = self.session.get(AIEmployee, assignment.ai_employee_id)
        capability = self.session.get(Capability, assignment.capability_id)
        if employee is None or capability is None:
            raise SalesWorkItemExecutionAssignmentError(
                "Sales WorkItem assignment is incomplete"
            )
        if (
            assignment.workspace_id != workspace.id
            or employee.workspace_id != workspace.id
            or capability.workspace_id != workspace.id
        ):
            raise SalesWorkItemExecutionScopeError(
                "Sales WorkItem assignment does not belong to this workspace"
            )
        if (
            employee.department_id != work_item.department_id
            or capability.department_id != work_item.department_id
            or employee.department_id != capability.department_id
        ):
            raise SalesWorkItemExecutionScopeError(
                "Sales WorkItem assignment does not belong to its Department"
            )
        if (
            assignment.ai_employee_id != work_item.ai_employee_id
            or assignment.capability_id != work_item.capability_id
        ):
            raise SalesWorkItemExecutionAssignmentError(
                "Sales WorkItem fields do not match its assignment"
            )
        if not employee.active or not capability.active:
            raise SalesWorkItemExecutionAssignmentError(
                "Sales WorkItem assignment is inactive"
            )
        try:
            capability_key = BusinessCapabilityKey(capability.key)
        except (TypeError, ValueError) as exc:
            raise SalesWorkItemUnsupportedCapabilityError(
                "Sales WorkItem capability is not supported for execution"
            ) from exc
        if capability_key not in M09_SALES_EXECUTION_CAPABILITIES:
            raise SalesWorkItemUnsupportedCapabilityError(
                "Sales WorkItem capability is not supported for execution"
            )
        return _ExecutionTarget(
            work_item=work_item,
            assignment=assignment,
            employee=employee,
            capability=capability,
            capability_key=capability_key,
        )

    def _validate_input(
        self,
        workspace: Workspace,
        work_item: WorkItem,
        capability_key: BusinessCapabilityKey,
    ) -> None:
        self._lead(workspace, work_item)
        if capability_key == BusinessCapabilityKey.QUALIFY_LEAD:
            research = work_item.input.get("research")
            if not isinstance(research, dict):
                raise SalesWorkItemInputError(
                    "Qualification WorkItem input requires research"
                )
        if capability_key == BusinessCapabilityKey.ANSWER_CUSTOMER:
            self._required_text(work_item, "channel", max_length=50)
            self._required_text(work_item, "customer_message", max_length=10_000)

    async def _execute_research(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        lead = self._lead(workspace, work_item)
        return await LeadResearchAgent(
            self._agent_context(workspace, work_item)
        ).run(lead)

    async def _execute_qualification(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        lead = self._lead(workspace, work_item)
        research = dict(work_item.input["research"])
        result = await QualificationAgent(
            self._agent_context(workspace, work_item)
        ).run(
            lead,
            research,
        )
        return {
            "score": result.score,
            "qualified": result.qualified,
            "reasons": list(result.reasons),
        }

    async def _execute_conversation(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        lead = self._lead(workspace, work_item)
        result = await SalesConversationTurnService(
            repository=self.repository,
            settings=self.settings,
            workspace=workspace,
            ai_invocation_gateway=self.ai_invocation_gateway,
            ai_execution_attribution=self.attribution.from_work_item(
                workspace,
                work_item,
            ),
        ).process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel=self._required_text(work_item, "channel", max_length=50),
                customer_message=self._required_text(
                    work_item,
                    "customer_message",
                    max_length=10_000,
                ),
            )
        )
        return {
            "lead_id": str(result.lead_id),
            "detected_stage": result.detected_stage.value,
            "draft_reply": result.draft_reply,
            "approval_id": str(result.approval_id) if result.approval_id else None,
            "handoff_required": result.handoff_required,
            "handoff_reason_code": (
                result.handoff_reason_code.value
                if result.handoff_reason_code is not None
                else None
            ),
            "ai_invoked": result.ai_invoked,
        }

    def _agent_context(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> AgentContext:
        return AgentContext(
            settings=self.settings,
            repository=self.repository,
            llm=None,
            workspace=workspace,
            ai_invocation_gateway=self.ai_invocation_gateway,
            ai_execution_attribution=self.attribution.from_work_item(
                workspace,
                work_item,
            ),
        )

    def _lead(self, workspace: Workspace, work_item: WorkItem) -> Lead:
        lead_id = self._required_uuid(work_item, "lead_id")
        try:
            lead = self.repository.get_lead(lead_id)
        except NotFoundError as exc:
            raise SalesWorkItemInputError("Sales WorkItem lead was not found") from exc
        if lead.tenant_id != workspace.slug:
            raise SalesWorkItemExecutionScopeError(
                "Sales WorkItem lead does not belong to this workspace"
            )
        return lead

    @staticmethod
    def _required_uuid(work_item: WorkItem, field: str) -> UUID:
        value = work_item.input.get(field)
        try:
            return UUID(str(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise SalesWorkItemInputError(
                f"Sales WorkItem input requires a valid {field}"
            ) from exc

    @staticmethod
    def _required_text(
        work_item: WorkItem,
        field: str,
        *,
        max_length: int,
    ) -> str:
        value = work_item.input.get(field)
        if not isinstance(value, str):
            raise SalesWorkItemInputError(
                f"Sales WorkItem input requires {field}"
            )
        normalized = value.strip()
        if not normalized or len(normalized) > max_length:
            raise SalesWorkItemInputError(
                f"Sales WorkItem input contains invalid {field}"
            )
        return normalized

    @staticmethod
    def _json_safe_result(result: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(json.dumps(result))
        except (TypeError, ValueError) as exc:
            raise SalesWorkItemResultError(
                "Sales WorkItem execution returned a non-JSON-safe result"
            ) from exc
        if not isinstance(value, dict):
            raise SalesWorkItemResultError(
                "Sales WorkItem execution result must be an object"
            )
        return value

    @staticmethod
    def _bounded_error_message(exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        return message[:500]
