from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from sqlmodel import Session

from app.config import Settings
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.comment_triggers import CommentTriggerResult
from app.core.work_items import WorkItemStatus
from app.models import (
    AIEmployee,
    AIEmployeeCapabilityAssignment,
    Capability,
    Department,
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationActionType,
    WorkItem,
    Workspace,
)
from app.services.ai_employee_tool_access import (
    AIEmployeeCapabilityToolAccessService,
)
from app.services.outbound_delivery import OutboundIntegrationDeliveryService
from app.services.outbound_integrations import OutboundIntegrationService
from app.services.work_item_approvals import WorkItemApprovalService
from app.services.work_items import WorkItemService


@dataclass(frozen=True, slots=True)
class SendMessageWorkItemResult:
    outcome: CommentTriggerResult
    work_item: WorkItem
    approval_id: UUID | None = None
    outbound_action: OutboundIntegrationAction | None = None


class SendMessageWorkItemService:
    """Execute one governed send_message assignment through existing boundaries."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.work_items = WorkItemService(session)

    def execute(
        self,
        workspace: Workspace,
        department: Department,
        capability: Capability,
        assignment: AIEmployeeCapabilityAssignment,
        account: IntegrationAccount,
        *,
        message: str,
        external_target_id: str,
        input: dict,
        idempotency_source: str,
        correlation_id: UUID,
    ) -> SendMessageWorkItemResult:
        work_item = self.work_items.create_work_item(
            workspace,
            department,
            work_type="social_comment_dm",
            title="Send comment-triggered message",
            capability=capability,
            input=dict(input),
        )
        work_item = self.work_items.assign_work_item(workspace, work_item.id, assignment)
        return self.execute_work_item(
            workspace,
            work_item.id,
            account,
            message=message,
            external_target_id=external_target_id,
            idempotency_source=idempotency_source,
            correlation_id=correlation_id,
        )

    def execute_work_item(
        self,
        workspace: Workspace,
        work_item_id: UUID,
        account: IntegrationAccount,
        *,
        message: str | None = None,
        external_target_id: str | None = None,
        idempotency_source: str | None = None,
        correlation_id: UUID | None = None,
        approval_id: UUID | None = None,
    ) -> SendMessageWorkItemResult:
        work_item = self.work_items.get_work_item(workspace, work_item_id)
        approval_authorized = approval_id is not None
        approval_payload: dict = {}
        if approval_authorized:
            if WorkItemStatus(work_item.status) != WorkItemStatus.APPROVAL_REQUIRED:
                raise ValueError(
                    "send_message WorkItem must require approval before approved execution"
                )
            resumed = WorkItemApprovalService(self.session).resume_after_approval(
                workspace,
                work_item.id,
                approval_id,
            )
            approval_payload = dict(resumed.approval.payload)
            work_item = self.work_items.get_work_item(workspace, work_item.id)
        elif WorkItemStatus(work_item.status) != WorkItemStatus.ASSIGNED:
            raise ValueError("send_message WorkItem must be assigned before execution")
        if work_item.assignment_id is None:
            raise ValueError("send_message WorkItem requires an assignment")
        assignment = self.session.get(AIEmployeeCapabilityAssignment, work_item.assignment_id)
        capability = (
            self.session.get(Capability, assignment.capability_id)
            if assignment is not None
            else None
        )
        employee = (
            self.session.get(AIEmployee, assignment.ai_employee_id)
            if assignment is not None
            else None
        )
        if (
            assignment is None
            or capability is None
            or employee is None
            or assignment.workspace_id != workspace.id
            or capability.workspace_id != workspace.id
            or employee.workspace_id != workspace.id
            or capability.department_id != work_item.department_id
            or employee.department_id != work_item.department_id
            or capability.key != "send_message"
            or not capability.active
            or not employee.active
        ):
            raise PermissionError("send_message WorkItem assignment is invalid")
        stored_account = self.session.get(IntegrationAccount, account.id)
        if stored_account is None or stored_account.workspace_id != workspace.id:
            raise PermissionError("IntegrationAccount does not belong to this workspace")
        configured_account_id = work_item.input.get("integration_account_id")
        if configured_account_id is not None and str(stored_account.id) != str(
            configured_account_id
        ):
            raise PermissionError(
                "send_message WorkItem is configured for another IntegrationAccount"
            )
        message = (
            message
            or self._optional_text(approval_payload.get("message"))
            or self._required_input_text(work_item, "message")
        )
        external_target_id = (
            external_target_id
            or self._optional_text(approval_payload.get("external_target_id"))
            or self._required_input_text(work_item, "recipient")
        )
        idempotency_source = idempotency_source or str(work_item.id)
        correlation_id = correlation_id or work_item.correlation_id
        if WorkItemStatus(work_item.status) == WorkItemStatus.ASSIGNED:
            work_item = self.work_items.transition_work_item(
                workspace, work_item.id, WorkItemStatus.RUNNING
            )

        try:
            decision = AIEmployeeCapabilityToolAccessService(self.session).evaluate(
                workspace,
                assignment,
                stored_account,
                OutboundIntegrationActionType.SEND_MESSAGE,
            )
            if not decision.allowed:
                failed = self._fail(
                    workspace,
                    work_item,
                    "tool_access_denied",
                    "AIEmployee is not allowed to use this IntegrationAccount",
                )
                return SendMessageWorkItemResult(
                    outcome=CommentTriggerResult.TOOL_ACCESS_DENIED,
                    work_item=failed,
                )

            if decision.autonomy_level == AIEmployeeAutonomyLevel.SUGGEST:
                completed = self.work_items.transition_work_item(
                    workspace,
                    work_item.id,
                    WorkItemStatus.COMPLETED,
                    result={"outcome": "suggested", "delivered": False},
                )
                return SendMessageWorkItemResult(
                    outcome=CommentTriggerResult.SUGGESTED,
                    work_item=completed,
                )

            if decision.requires_human_approval and not approval_authorized:
                approval = WorkItemApprovalService(self.session).request_approval(
                    workspace,
                    work_item.id,
                    action_type=OutboundIntegrationActionType.SEND_MESSAGE.value,
                    channel=str(work_item.input["channel"]),
                    payload={
                        "integration_account_id": str(stored_account.id),
                        "external_target_id": external_target_id,
                        "message": message,
                    },
                )
                return SendMessageWorkItemResult(
                    outcome=CommentTriggerResult.APPROVAL_REQUIRED,
                    work_item=approval.work_item,
                    approval_id=approval.approval.id,
                )

            if not decision.may_execute_automatically and not approval_authorized:
                failed = self._fail(
                    workspace,
                    work_item,
                    "automation_not_permitted",
                    "AIEmployee autonomy does not permit automatic delivery",
                )
                return SendMessageWorkItemResult(
                    outcome=CommentTriggerResult.TOOL_ACCESS_DENIED,
                    work_item=failed,
                )

            action, _ = OutboundIntegrationService(self.session).create_action(
                workspace,
                stored_account.id,
                external_target_id=external_target_id,
                action_type=OutboundIntegrationActionType.SEND_MESSAGE,
                content=message,
                payload={
                    "channel": work_item.input["channel"],
                    "external_subject_id": work_item.input.get("external_subject_id"),
                    "comment_id": work_item.input.get("comment_id"),
                    "post_or_media_id": work_item.input.get("post_or_media_id"),
                    "lead_id": work_item.input.get("lead_id"),
                    "source_follow_up_task_id": work_item.input.get("source_follow_up_task_id"),
                    "source_follow_up_work_item_id": work_item.input.get(
                        "source_follow_up_work_item_id"
                    ),
                    "work_item_id": str(work_item.id),
                },
                correlation_id=str(correlation_id),
                idempotency_key=self._idempotency_key(idempotency_source),
            )
            delivered, _ = OutboundIntegrationDeliveryService.from_settings(
                self.session, self.settings
            ).deliver_pending_action(workspace, stored_account.id, action.id)
            if delivered.status == OutboundIntegrationActionStatus.DELIVERED:
                completed = self.work_items.transition_work_item(
                    workspace,
                    work_item.id,
                    WorkItemStatus.COMPLETED,
                    result={
                        "outcome": "outbound_delivered",
                        "outbound_action_id": str(delivered.id),
                    },
                )
                return SendMessageWorkItemResult(
                    outcome=CommentTriggerResult.OUTBOUND_DELIVERED,
                    work_item=completed,
                    outbound_action=delivered,
                )
            failed = self._fail(
                workspace,
                work_item,
                "outbound_delivery_failed",
                "Outbound message delivery failed",
            )
            return SendMessageWorkItemResult(
                outcome=CommentTriggerResult.OUTBOUND_FAILED,
                work_item=failed,
                outbound_action=delivered,
            )
        except Exception:  # noqa: BLE001 - persist a safe terminal state after capture.
            self.session.rollback()
            current = self.work_items.get_work_item(workspace, work_item.id)
            if WorkItemStatus(current.status) == WorkItemStatus.RUNNING:
                current = self._fail(
                    workspace,
                    current,
                    "send_message_execution_failed",
                    "Governed message execution failed",
                )
            return SendMessageWorkItemResult(
                outcome=CommentTriggerResult.OUTBOUND_FAILED,
                work_item=current,
            )

    def _fail(
        self,
        workspace: Workspace,
        work_item: WorkItem,
        code: str,
        message: str,
    ) -> WorkItem:
        return self.work_items.transition_work_item(
            workspace,
            work_item.id,
            WorkItemStatus.FAILED,
            error_code=code,
            error_message=message,
        )

    @staticmethod
    def _idempotency_key(source: str) -> str:
        return f"social-comment-dm:{sha256(source.encode()).hexdigest()}"

    @staticmethod
    def _required_input_text(work_item: WorkItem, field: str) -> str:
        value = work_item.input.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"send_message WorkItem input requires {field}")
        return value.strip()

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()
