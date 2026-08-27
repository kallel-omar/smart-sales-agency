from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings
from app.core.agent_skill_execution import AgentSkillExecutionContext
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.core.work_items import WorkItemStatus
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.agents.follow_up import FollowUpAgent
from app.departments.sales.agents.lead_researcher import LeadResearchAgent
from app.departments.sales.agents.qualifier import QualificationAgent
from app.departments.sales.conversation_expertise import (
    CONVERSATION_EXPERTISE_VERSION,
    select_sales_conversation_skill,
)
from app.departments.sales.pricing_explanation import (
    PRICING_EXPLANATION_KEY,
    PRICING_EXPLANATION_VERSION,
)
from app.departments.sales.services.conversation_turn_service import (
    SalesConversationTurnInput,
    SalesConversationTurnService,
)
from app.departments.sales.skills import sales_agent_skill_registry
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    Capability,
    Contact,
    Customer,
    Department,
    FollowUpTask,
    IntegrationAccount,
    Lead,
    LeadResearch,
    WorkItem,
    Workspace,
)
from app.services.agent_skill_execution import AgentSkillExecutionContextResolver
from app.services.ai_execution_attribution import AIExecutionAttributionService
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.department_supervisors import DepartmentSupervisorRoutingService
from app.services.repository import NotFoundError, SalesRepository
from app.services.send_message_work_items import SendMessageWorkItemService
from app.services.work_items import WorkItemService

M09_SALES_EXECUTION_CAPABILITIES = frozenset(
    {
        BusinessCapabilityKey.RESEARCH_COMPANY,
        BusinessCapabilityKey.QUALIFY_LEAD,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    }
)
SALES_EXECUTION_CAPABILITIES = M09_SALES_EXECUTION_CAPABILITIES | {
    BusinessCapabilityKey.CAPTURE_LEAD,
    BusinessCapabilityKey.FOLLOW_UP_LEAD,
}


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
        settings: Settings | None,
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
        skill_context = self._agent_skill_context(workspace, target)
        self.work_items.transition_work_item(
            workspace,
            work_item_id,
            WorkItemStatus.RUNNING,
        )
        try:
            if skill_context is not None:
                raw_result = await self._execute_conversation(
                    workspace,
                    target.work_item,
                    agent_skill_execution_context=skill_context,
                )
            else:
                raw_result = await executor(workspace, target.work_item)
            result = self._json_safe_result(raw_result)
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

    def execute_capture(
        self,
        workspace: Workspace,
        work_item_id: UUID,
    ) -> WorkItem:
        """Execute deterministic capture from synchronous channel boundaries."""

        target = self._execution_target(workspace, work_item_id)
        if target.capability_key != BusinessCapabilityKey.CAPTURE_LEAD:
            raise SalesWorkItemUnsupportedCapabilityError(
                "Synchronous capture execution requires capture_lead"
            )
        self._validate_input(workspace, target.work_item, target.capability_key)
        self.work_items.transition_work_item(
            workspace,
            work_item_id,
            WorkItemStatus.RUNNING,
        )
        try:
            result = self._json_safe_result(
                self._capture_result(workspace, target.work_item)
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
            BusinessCapabilityKey.CAPTURE_LEAD: self._execute_capture,
            BusinessCapabilityKey.RESEARCH_COMPANY: self._execute_research,
            BusinessCapabilityKey.QUALIFY_LEAD: self._execute_qualification,
            BusinessCapabilityKey.ANSWER_CUSTOMER: self._execute_conversation,
            BusinessCapabilityKey.FOLLOW_UP_LEAD: self._execute_follow_up,
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
            raise SalesWorkItemExecutionAssignmentError("Sales WorkItem assignment was not found")
        employee = self.session.get(AIEmployee, assignment.ai_employee_id)
        capability = self.session.get(Capability, assignment.capability_id)
        if employee is None or capability is None:
            raise SalesWorkItemExecutionAssignmentError("Sales WorkItem assignment is incomplete")
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
            raise SalesWorkItemExecutionAssignmentError("Sales WorkItem assignment is inactive")
        try:
            capability_key = BusinessCapabilityKey(capability.key)
        except (TypeError, ValueError) as exc:
            raise SalesWorkItemUnsupportedCapabilityError(
                "Sales WorkItem capability is not supported for execution"
            ) from exc
        if capability_key not in SALES_EXECUTION_CAPABILITIES:
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
            if work_item.input.get("lead_research_id") is None and not isinstance(
                research, dict
            ):
                raise SalesWorkItemInputError("Qualification WorkItem input requires research")
        if capability_key == BusinessCapabilityKey.ANSWER_CUSTOMER:
            self._required_text(work_item, "channel", max_length=50)
            self._required_text(work_item, "customer_message", max_length=10_000)
        if capability_key == BusinessCapabilityKey.FOLLOW_UP_LEAD:
            task_id = self._required_uuid(work_item, "follow_up_task_id")
            task = self.session.get(FollowUpTask, task_id)
            if (
                task is None
                or task.lead_id != self._required_uuid(work_item, "lead_id")
                or work_item.source_follow_up_task_id != task.id
            ):
                raise SalesWorkItemInputError("Follow-up WorkItem does not match its FollowUpTask")

    async def _execute_capture(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        return self._capture_result(workspace, work_item)

    def _capture_result(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        lead = self._lead(workspace, work_item)
        result: dict[str, Any] = {
            "lead_id": str(lead.id),
            "source": self._required_text(work_item, "source", max_length=100),
            "customer_created": bool(work_item.input.get("customer_created", False)),
            "contact_created": bool(work_item.input.get("contact_created", False)),
            "lead_created": bool(work_item.input.get("lead_created", False)),
        }
        contact_value = work_item.input.get("contact_id")
        if contact_value is not None:
            contact_id = self._uuid_value(contact_value, "contact_id")
            contact = self.session.get(Contact, contact_id)
            if contact is None or contact.workspace_id != workspace.id:
                raise SalesWorkItemExecutionScopeError(
                    "Capture WorkItem Contact does not belong to this workspace"
                )
            if lead.contact_id is not None and lead.contact_id != contact.id:
                raise SalesWorkItemInputError("Capture WorkItem Contact does not match its Lead")
            result["contact_id"] = str(contact.id)
        customer_value = work_item.input.get("customer_id")
        if customer_value is not None:
            customer_id = self._uuid_value(customer_value, "customer_id")
            customer = self.session.get(Customer, customer_id)
            if customer is None or customer.workspace_id != workspace.id:
                raise SalesWorkItemExecutionScopeError(
                    "Capture WorkItem Customer does not belong to this workspace"
                )
            result["customer_id"] = str(customer.id)
        metadata = work_item.input.get("metadata")
        if isinstance(metadata, dict):
            result["source_metadata"] = dict(metadata)
        return result

    async def _execute_research(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        lead = self._lead(workspace, work_item)
        return await LeadResearchAgent(self._agent_context(workspace, work_item)).run(lead)

    async def _execute_qualification(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        lead = self._lead(workspace, work_item)
        research = self._qualification_research(workspace, lead, work_item)
        result = await QualificationAgent(self._agent_context(workspace, work_item)).run(
            lead,
            research,
        )
        return {
            "score": result.score,
            "qualified": result.qualified,
            "reasons": list(result.reasons),
            "outcome": "qualified" if result.qualified else "unqualified",
        }

    def _qualification_research(
        self,
        workspace: Workspace,
        lead: Lead,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        research_id_value = work_item.input.get("lead_research_id")
        if research_id_value is None:
            return dict(work_item.input["research"])
        research_id = self._uuid_value(research_id_value, "lead_research_id")
        research = self.session.get(LeadResearch, research_id)
        if research is None or research.lead_id != lead.id or lead.tenant_id != workspace.slug:
            raise SalesWorkItemExecutionScopeError(
                "Qualification research does not belong to this workspace Lead"
            )
        return {
            "summary": research.summary,
            "pain_points": list(research.pain_points),
            "opportunities": list(research.opportunities),
            "evidence": list(research.evidence),
        }

    async def _execute_conversation(
        self,
        workspace: Workspace,
        work_item: WorkItem,
        *,
        agent_skill_execution_context: AgentSkillExecutionContext | None = None,
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
            agent_skill_execution_context=agent_skill_execution_context,
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
        response: dict[str, Any] = {
            "lead_id": str(result.lead_id),
            "detected_stage": result.detected_stage.value,
            "draft_reply": result.draft_reply,
            "approval_id": str(result.approval_id) if result.approval_id else None,
            "handoff_required": result.handoff_required,
            "handoff_reason_code": (
                result.handoff_reason_code.value if result.handoff_reason_code is not None else None
            ),
            "ai_invoked": result.ai_invoked,
        }
        if result.agent_skill is not None:
            response["agent_skill"] = {
                "key": result.agent_skill.key,
                "version": result.agent_skill.version,
                "outcome": result.agent_skill.outcome,
                "validation_outcome": result.agent_skill.validation_outcome,
            }
            if result.agent_skill.structured_result is not None:
                response["agent_skill"]["result"] = result.agent_skill.structured_result
        return response

    def _agent_skill_context(
        self,
        workspace: Workspace,
        target: _ExecutionTarget,
    ) -> AgentSkillExecutionContext | None:
        if target.capability_key is not BusinessCapabilityKey.ANSWER_CUSTOMER:
            return None
        customer_message = self._required_text(
            target.work_item,
            "customer_message",
            max_length=10_000,
        )
        skill_key = select_sales_conversation_skill(customer_message)
        if skill_key is None:
            return None
        skill_version = (
            PRICING_EXPLANATION_VERSION
            if skill_key == PRICING_EXPLANATION_KEY
            else CONVERSATION_EXPERTISE_VERSION
        )
        return AgentSkillExecutionContextResolver(
            self.session,
            sales_agent_skill_registry(),
        ).resolve(
            workspace,
            target.work_item.id,
            skill_key=skill_key,
            skill_version=skill_version,
        )

    async def _execute_follow_up(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        lead = self._lead(workspace, work_item)
        task_id = self._required_uuid(work_item, "follow_up_task_id")
        task = self.session.get(FollowUpTask, task_id)
        if task is None:
            raise SalesWorkItemInputError("FollowUpTask was not found")
        decision = FollowUpAgent(self.session).decide(task, lead, work_item.input)
        result: dict[str, Any] = {
            "action": decision["action"],
            "lead_id": str(lead.id),
            "follow_up_task_id": str(task.id),
            "reason": decision["reason"],
        }
        if decision["action"] == "no_send":
            task.status = "completed"
            self.session.add(task)
            self.session.commit()
            return result

        if decision["action"] != "send":
            raise SalesWorkItemResultError("Follow-up decision action is unsupported")

        child = self._get_or_create_send_work_item(workspace, work_item, lead, task, decision)
        if WorkItemStatus(child.status) == WorkItemStatus.CREATED:
            DepartmentSupervisorRoutingService(self.session).route_and_assign(workspace, child.id)
            child = self.work_items.get_work_item(workspace, child.id)
        result.update(
            {
                "message": decision["message"],
                "integration_account_id": decision["integration_account_id"],
                "channel": decision["channel"],
                "recipient": decision["recipient"],
                "send_work_item_id": str(child.id),
            }
        )
        if WorkItemStatus(child.status) != WorkItemStatus.ASSIGNED:
            result["send_work_item_status"] = WorkItemStatus(child.status).value
            result["reason"] = "send_message_assignment_unavailable"
            return result

        account_id = self._uuid_value(decision["integration_account_id"], "integration_account_id")
        account = self.session.get(IntegrationAccount, account_id)
        if account is None or account.workspace_id != workspace.id:
            raise SalesWorkItemExecutionScopeError(
                "Follow-up IntegrationAccount does not belong to this workspace"
            )
        send = SendMessageWorkItemService(self.session, self.settings).execute_work_item(
            workspace,
            child.id,
            account,
            idempotency_source=f"follow-up:{task.id}:{child.id}",
            correlation_id=work_item.correlation_id,
        )
        result.update(
            {
                "send_outcome": send.outcome.value,
                "send_work_item_status": WorkItemStatus(send.work_item.status).value,
                "approval_id": str(send.approval_id) if send.approval_id else None,
                "outbound_action_id": (
                    str(send.outbound_action.id) if send.outbound_action else None
                ),
            }
        )
        if send.outcome.value == "outbound_delivered":
            task.status = "completed"
            self.session.add(task)
            self.session.commit()
        return result

    def _get_or_create_send_work_item(
        self,
        workspace: Workspace,
        source: WorkItem,
        lead: Lead,
        task: FollowUpTask,
        decision: dict[str, Any],
    ) -> WorkItem:
        existing = self.session.exec(
            select(WorkItem).where(
                WorkItem.workspace_id == workspace.id,
                WorkItem.parent_work_item_id == source.id,
            )
        ).first()
        if existing is not None:
            return existing
        capability = self.session.exec(
            select(Capability).where(
                Capability.workspace_id == workspace.id,
                Capability.department_id == source.department_id,
                Capability.key == BusinessCapabilityKey.SEND_MESSAGE,
                Capability.active.is_(True),
            )
        ).first()
        department = self.session.get(Department, source.department_id)
        if capability is None or department is None:
            raise SalesWorkItemExecutionAssignmentError(
                "send_message Capability is not configured for Sales"
            )
        safe_input = {
            "lead_id": str(lead.id),
            "source_follow_up_task_id": str(task.id),
            "source_follow_up_work_item_id": str(source.id),
            "integration_account_id": str(decision["integration_account_id"]),
            "channel": str(decision["channel"]),
            "recipient": str(decision["recipient"]),
            "message": str(decision["message"]),
        }
        try:
            return self.work_items.create_work_item(
                workspace,
                department,
                work_type="sales_follow_up_message",
                title="Send sales follow-up message",
                capability=capability,
                input=safe_input,
                parent_work_item_id=source.id,
            )
        except IntegrityError:
            self.session.rollback()
            existing = self.session.exec(
                select(WorkItem).where(
                    WorkItem.workspace_id == workspace.id,
                    WorkItem.parent_work_item_id == source.id,
                )
            ).first()
            if existing is None:
                raise
            return existing

    @staticmethod
    def _uuid_value(value: Any, field: str) -> UUID:
        try:
            return UUID(str(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise SalesWorkItemInputError(f"Sales WorkItem input requires a valid {field}") from exc

    def _agent_context(
        self,
        workspace: Workspace,
        work_item: WorkItem,
    ) -> AgentContext:
        if self.settings is None:
            raise SalesWorkItemExecutionStateError(
                "Sales AI execution requires runtime settings"
            )
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
            raise SalesWorkItemInputError(f"Sales WorkItem input requires a valid {field}") from exc

    @staticmethod
    def _required_text(
        work_item: WorkItem,
        field: str,
        *,
        max_length: int,
    ) -> str:
        value = work_item.input.get(field)
        if not isinstance(value, str):
            raise SalesWorkItemInputError(f"Sales WorkItem input requires {field}")
        normalized = value.strip()
        if not normalized or len(normalized) > max_length:
            raise SalesWorkItemInputError(f"Sales WorkItem input contains invalid {field}")
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
            raise SalesWorkItemResultError("Sales WorkItem execution result must be an object")
        return value

    @staticmethod
    def _bounded_error_message(exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        return message[:500]
