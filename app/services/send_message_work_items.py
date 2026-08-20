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
        work_item = self.work_items.transition_work_item(
            workspace, work_item.id, WorkItemStatus.RUNNING
        )

        try:
            decision = AIEmployeeCapabilityToolAccessService(self.session).evaluate(
                workspace,
                assignment,
                account,
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

            if decision.requires_human_approval:
                approval = WorkItemApprovalService(self.session).request_approval(
                    workspace,
                    work_item.id,
                    action_type=OutboundIntegrationActionType.SEND_MESSAGE.value,
                    channel=str(input["channel"]),
                    payload={
                        "integration_account_id": str(account.id),
                        "external_target_id": external_target_id,
                        "message": message,
                    },
                )
                return SendMessageWorkItemResult(
                    outcome=CommentTriggerResult.APPROVAL_REQUIRED,
                    work_item=approval.work_item,
                    approval_id=approval.approval.id,
                )

            if not decision.may_execute_automatically:
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
                account.id,
                external_target_id=external_target_id,
                action_type=OutboundIntegrationActionType.SEND_MESSAGE,
                content=message,
                payload={
                    "channel": input["channel"],
                    "external_subject_id": input["external_subject_id"],
                    "comment_id": input["comment_id"],
                    "post_or_media_id": input.get("post_or_media_id"),
                    "work_item_id": str(work_item.id),
                },
                correlation_id=str(correlation_id),
                idempotency_key=self._idempotency_key(idempotency_source),
            )
            delivered, _ = OutboundIntegrationDeliveryService.from_settings(
                self.session, self.settings
            ).deliver_pending_action(workspace, account.id, action.id)
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
