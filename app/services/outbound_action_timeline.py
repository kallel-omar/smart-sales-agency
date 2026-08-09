"""Read-only, safe chronological timelines for outbound delivery actions."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from sqlmodel import Session

from app.models import (
    ApprovalStatus,
    OutboundIntegrationActionStatus,
    OutboundIntegrationAuditAction,
    Workspace,
)
from app.services.integration_accounts import IntegrationAccountService
from app.services.outbound_action_audit import OutboundIntegrationActionAuditService
from app.services.outbound_delivery import OutboundIntegrationDeliveryService
from app.services.outbound_delivery_approvals import OutboundDeliveryApprovalService


DEFAULT_OUTBOUND_ACTION_TIMELINE_LIMIT = 50
MAX_OUTBOUND_ACTION_TIMELINE_LIMIT = 100


class OutboundActionTimelineCategory(StrEnum):
    """Stable, provider-neutral groups for timeline entries."""

    LIFECYCLE = "lifecycle"
    DELIVERY = "delivery"
    RETRY = "retry"
    APPROVAL = "approval"


class OutboundActionTimelineEvent(StrEnum):
    """Stable, provider-neutral timeline event codes."""

    ACTION_CREATED = "action_created"
    DELIVERY_ATTEMPTED = "delivery_attempted"
    ACTION_DELIVERED = "action_delivered"
    ACTION_FAILED = "action_failed"
    DELIVERY_RETRIED = "delivery_retried"
    ACTION_CANCELLED = "action_cancelled"
    ACTION_EXPIRED = "action_expired"
    DELIVERY_ATTEMPT = "delivery_attempt"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXECUTED = "approval_executed"


@dataclass(frozen=True)
class OutboundActionTimelineEntry:
    """Safe entry composed from existing action lifecycle records."""

    category: OutboundActionTimelineCategory
    event: OutboundActionTimelineEvent
    message: str
    created_at: datetime
    state: OutboundIntegrationActionStatus | None = None
    attempt_number: int | None = None


@dataclass(frozen=True)
class _TimelineEntryWithSortKey:
    entry: OutboundActionTimelineEntry
    source_order: int
    source_id: str


class OutboundActionTimelineService:
    """Compose persisted safe records without creating a timeline table."""

    _AUDIT_EVENT_DETAILS = {
        OutboundIntegrationAuditAction.CREATED: (
            OutboundActionTimelineCategory.LIFECYCLE,
            OutboundActionTimelineEvent.ACTION_CREATED,
            "The outbound action was created.",
            None,
        ),
        OutboundIntegrationAuditAction.DELIVERY_ATTEMPTED: (
            OutboundActionTimelineCategory.DELIVERY,
            OutboundActionTimelineEvent.DELIVERY_ATTEMPTED,
            "Outbound delivery was attempted.",
            None,
        ),
        OutboundIntegrationAuditAction.DELIVERED: (
            OutboundActionTimelineCategory.LIFECYCLE,
            OutboundActionTimelineEvent.ACTION_DELIVERED,
            "The outbound action was delivered.",
            OutboundIntegrationActionStatus.DELIVERED,
        ),
        OutboundIntegrationAuditAction.FAILED: (
            OutboundActionTimelineCategory.LIFECYCLE,
            OutboundActionTimelineEvent.ACTION_FAILED,
            "Outbound delivery failed.",
            OutboundIntegrationActionStatus.FAILED,
        ),
        OutboundIntegrationAuditAction.RETRIED: (
            OutboundActionTimelineCategory.RETRY,
            OutboundActionTimelineEvent.DELIVERY_RETRIED,
            "The outbound action was retried.",
            None,
        ),
        OutboundIntegrationAuditAction.CANCELLED: (
            OutboundActionTimelineCategory.LIFECYCLE,
            OutboundActionTimelineEvent.ACTION_CANCELLED,
            "The outbound action was cancelled.",
            OutboundIntegrationActionStatus.CANCELLED,
        ),
        OutboundIntegrationAuditAction.EXPIRED: (
            OutboundActionTimelineCategory.LIFECYCLE,
            OutboundActionTimelineEvent.ACTION_EXPIRED,
            "The outbound action expired.",
            OutboundIntegrationActionStatus.EXPIRED,
        ),
    }

    def __init__(self, session: Session) -> None:
        self.account_service = IntegrationAccountService(session)
        self.delivery_service = OutboundIntegrationDeliveryService(session)
        self.audit_service = OutboundIntegrationActionAuditService(session)
        self.approval_service = OutboundDeliveryApprovalService(session)

    def list_for_action(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
        *,
        category: OutboundActionTimelineCategory | None = None,
        event: OutboundActionTimelineEvent | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = DEFAULT_OUTBOUND_ACTION_TIMELINE_LIMIT,
    ) -> list[OutboundActionTimelineEntry]:
        """Return a bounded, oldest-first timeline from the scoped action only."""
        self._validate_query(created_after, created_before, limit)
        account = self.account_service.get_for_workspace(workspace, account_id)
        action = self.delivery_service._get_action_for_account(workspace, account, action_id)
        entries = self._audit_entries(workspace, account.id, action.id)
        entries.extend(self._attempt_entries(workspace, account.id, action.id))
        entries.extend(self._approval_entries(workspace, action))
        entries = [
            item
            for item in entries
            if (category is None or item.entry.category == category)
            and (event is None or item.entry.event == event)
            and self._in_time_range(
                item.entry.created_at,
                created_after,
                created_before,
            )
        ]
        entries.sort(
            key=lambda item: (
                item.entry.created_at,
                item.source_order,
                item.source_id,
            )
        )
        return [item.entry for item in entries[:limit]]

    def _audit_entries(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
    ) -> list[_TimelineEntryWithSortKey]:
        events = self.audit_service.list_for_workspace(
            workspace,
            integration_account_id=account_id,
            outbound_integration_action_id=action_id,
            limit=MAX_OUTBOUND_ACTION_TIMELINE_LIMIT,
        )
        return [
            _TimelineEntryWithSortKey(
                entry=OutboundActionTimelineEntry(
                    category=self._AUDIT_EVENT_DETAILS[audit.action][0],
                    event=self._AUDIT_EVENT_DETAILS[audit.action][1],
                    message=self._AUDIT_EVENT_DETAILS[audit.action][2],
                    state=self._AUDIT_EVENT_DETAILS[audit.action][3],
                    created_at=self._as_utc(audit.created_at),
                ),
                source_order=1,
                source_id=str(audit.id),
            )
            for audit in events
        ]

    def _attempt_entries(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
    ) -> list[_TimelineEntryWithSortKey]:
        attempts = self.delivery_service.list_attempts_for_action(
            workspace,
            account_id,
            action_id,
            limit=MAX_OUTBOUND_ACTION_TIMELINE_LIMIT,
        )
        return [
            _TimelineEntryWithSortKey(
                entry=OutboundActionTimelineEntry(
                    category=OutboundActionTimelineCategory.DELIVERY,
                    event=OutboundActionTimelineEvent.DELIVERY_ATTEMPT,
                    message="Outbound delivery attempt was recorded.",
                    state=attempt.status,
                    attempt_number=attempt.attempt_number,
                    created_at=self._as_utc(attempt.started_at),
                ),
                source_order=2,
                source_id=str(attempt.id),
            )
            for attempt in attempts
        ]

    def _approval_entries(
        self,
        workspace: Workspace,
        action,
    ) -> list[_TimelineEntryWithSortKey]:
        approval = self.approval_service.get_for_action(workspace, action)
        if approval is None:
            return []
        entries = [
            _TimelineEntryWithSortKey(
                entry=OutboundActionTimelineEntry(
                    category=OutboundActionTimelineCategory.APPROVAL,
                    event=OutboundActionTimelineEvent.APPROVAL_REQUESTED,
                    message="Outbound delivery approval was requested.",
                    created_at=self._as_utc(approval.created_at),
                ),
                source_order=0,
                source_id=str(approval.id),
            )
        ]
        decision = self._approval_decision_entry(approval)
        if decision is not None:
            entries.append(decision)
        return entries

    def _approval_decision_entry(self, approval) -> _TimelineEntryWithSortKey | None:
        decision_details = {
            ApprovalStatus.APPROVED: (
                OutboundActionTimelineEvent.APPROVAL_APPROVED,
                "Outbound delivery approval was approved.",
            ),
            ApprovalStatus.REJECTED: (
                OutboundActionTimelineEvent.APPROVAL_REJECTED,
                "Outbound delivery approval was rejected.",
            ),
            ApprovalStatus.EXECUTED: (
                OutboundActionTimelineEvent.APPROVAL_EXECUTED,
                "Outbound delivery approval was executed.",
            ),
        }
        detail = decision_details.get(approval.status)
        if detail is None or approval.decided_at is None:
            return None
        return _TimelineEntryWithSortKey(
            entry=OutboundActionTimelineEntry(
                category=OutboundActionTimelineCategory.APPROVAL,
                event=detail[0],
                message=detail[1],
                created_at=self._as_utc(approval.decided_at),
            ),
            source_order=0,
            source_id=f"{approval.id}:decision",
        )

    @staticmethod
    def _validate_query(
        created_after: datetime | None,
        created_before: datetime | None,
        limit: int,
    ) -> None:
        if not 1 <= limit <= MAX_OUTBOUND_ACTION_TIMELINE_LIMIT:
            raise ValueError(
                "Outbound action timeline limit must be between 1 and "
                f"{MAX_OUTBOUND_ACTION_TIMELINE_LIMIT}"
            )
        if (
            created_after is not None
            and created_before is not None
            and OutboundActionTimelineService._as_utc(created_after)
            > OutboundActionTimelineService._as_utc(created_before)
        ):
            raise ValueError("created_after must not be later than created_before")

    @staticmethod
    def _in_time_range(
        value: datetime,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> bool:
        if created_after is not None and value < OutboundActionTimelineService._as_utc(
            created_after
        ):
            return False
        return created_before is None or value <= OutboundActionTimelineService._as_utc(
            created_before
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
