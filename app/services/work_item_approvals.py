from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.core.events import BusinessEvent, Department as DepartmentKind
from app.core.work_items import WorkItemStatus
from app.models import ApprovalRequest, ApprovalStatus, Department, WorkItem, Workspace
from app.services.work_items import WorkItemService

WORK_ITEM_APPROVAL_REQUESTED_EVENT = "work_item.approval_requested"
WORK_ITEM_APPROVAL_RESUME_PERMITTED_EVENT = "work_item.approval_resume_permitted"
WORK_ITEM_APPROVAL_RESUME_BLOCKED_EVENT = "work_item.approval_resume_blocked"

WorkItemApprovalEventRecorder = Callable[[BusinessEvent], None]


class WorkItemApprovalInvalidStateError(ValueError):
    """Raised when a WorkItem cannot enter or leave approval_required."""


class WorkItemApprovalNotFoundError(LookupError):
    """Raised when a WorkItem approval is absent from the requested workspace."""


class WorkItemApprovalScopeError(PermissionError):
    """Raised when a linked approval does not belong to the requested WorkItem."""


class WorkItemApprovalNotPermittedError(PermissionError):
    """Raised when approval state does not permit WorkItem resume."""


@dataclass(frozen=True)
class WorkItemApprovalRequestResult:
    work_item: WorkItem
    approval: ApprovalRequest
    event: BusinessEvent


@dataclass(frozen=True)
class WorkItemApprovalResumeResult:
    work_item: WorkItem
    approval: ApprovalRequest
    event: BusinessEvent


class WorkItemApprovalService:
    """Bridge WorkItems to the existing ApprovalRequest and BusinessEvent contracts."""

    def __init__(
        self,
        session: Session,
        *,
        event_recorder: WorkItemApprovalEventRecorder | None = None,
    ) -> None:
        self.session = session
        self.work_item_service = WorkItemService(session)
        self.event_recorder = event_recorder

    def request_approval(
        self,
        workspace: Workspace,
        work_item_id: UUID,
        *,
        action_type: str = "work_item_approval",
        channel: str = "work_item",
        payload: dict | None = None,
    ) -> WorkItemApprovalRequestResult:
        work_item = self.work_item_service.get_work_item(workspace, work_item_id)
        if WorkItemStatus(work_item.status) != WorkItemStatus.RUNNING:
            raise WorkItemApprovalInvalidStateError(
                "WorkItem must be running before approval can be requested"
            )
        approval = ApprovalRequest(
            work_item_id=work_item.id,
            action_type=self._bounded_text(action_type, "Approval action type", 100),
            channel=self._bounded_text(channel, "Approval channel", 50),
            payload=dict(payload or {}),
        )
        self.session.add(approval)
        self.session.flush()
        transitioned = self.work_item_service.transition_work_item(
            workspace,
            work_item.id,
            WorkItemStatus.APPROVAL_REQUIRED,
        )
        self.session.refresh(approval)
        event = self._event(
            workspace,
            transitioned,
            WORK_ITEM_APPROVAL_REQUESTED_EVENT,
            approval,
        )
        self._record(event)
        return WorkItemApprovalRequestResult(
            work_item=transitioned,
            approval=approval,
            event=event,
        )

    def resume_after_approval(
        self,
        workspace: Workspace,
        work_item_id: UUID,
        approval_id: UUID,
    ) -> WorkItemApprovalResumeResult:
        work_item = self.work_item_service.get_work_item(workspace, work_item_id)
        if WorkItemStatus(work_item.status) != WorkItemStatus.APPROVAL_REQUIRED:
            raise WorkItemApprovalInvalidStateError(
                "WorkItem must require approval before it can resume"
            )
        approval = self._get_scoped_linked_approval(workspace, work_item, approval_id)
        if approval.status not in {ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED}:
            event = self._event(
                workspace,
                work_item,
                WORK_ITEM_APPROVAL_RESUME_BLOCKED_EVENT,
                approval,
            )
            self._record(event)
            raise WorkItemApprovalNotPermittedError(
                "Approval state does not permit WorkItem resume"
            )
        transitioned = self.work_item_service.transition_work_item(
            workspace,
            work_item.id,
            WorkItemStatus.RUNNING,
        )
        event = self._event(
            workspace,
            transitioned,
            WORK_ITEM_APPROVAL_RESUME_PERMITTED_EVENT,
            approval,
        )
        self._record(event)
        return WorkItemApprovalResumeResult(
            work_item=transitioned,
            approval=approval,
            event=event,
        )

    def get_scoped_work_item_approval(
        self,
        workspace: Workspace,
        approval_id: UUID,
    ) -> ApprovalRequest:
        approval = self.session.exec(
            select(ApprovalRequest)
            .join(WorkItem, ApprovalRequest.work_item_id == WorkItem.id)
            .where(
                ApprovalRequest.id == approval_id,
                WorkItem.workspace_id == workspace.id,
            )
        ).first()
        if approval is None:
            raise WorkItemApprovalNotFoundError("WorkItem approval request not found")
        return approval

    def _get_scoped_linked_approval(
        self,
        workspace: Workspace,
        work_item: WorkItem,
        approval_id: UUID,
    ) -> ApprovalRequest:
        approval = self.get_scoped_work_item_approval(workspace, approval_id)
        if approval.work_item_id != work_item.id:
            raise WorkItemApprovalScopeError(
                "Approval request is not linked to this WorkItem"
            )
        return approval

    def _event(
        self,
        workspace: Workspace,
        work_item: WorkItem,
        event_type: str,
        approval: ApprovalRequest,
    ) -> BusinessEvent:
        department = self.session.get(Department, work_item.department_id)
        destination = (
            DepartmentKind(department.kind)
            if department is not None
            else DepartmentKind.PLATFORM
        )
        return BusinessEvent(
            workspace_id=workspace.id,
            correlation_id=work_item.correlation_id,
            event_type=event_type,
            source_department=DepartmentKind.PLATFORM,
            destination_department=destination,
            payload={
                "work_item_id": str(work_item.id),
                "approval_id": str(approval.id),
                "approval_status": ApprovalStatus(approval.status).value,
                "work_item_status": WorkItemStatus(work_item.status).value,
            },
        )

    def _record(self, event: BusinessEvent) -> None:
        if self.event_recorder is not None:
            self.event_recorder(event)

    @staticmethod
    def _bounded_text(value: str, label: str, max_length: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{label} is required")
        if len(normalized) > max_length:
            raise ValueError(f"{label} is too long")
        return normalized
