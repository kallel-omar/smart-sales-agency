"""Read-only operational aggregates for workspace integration accounts."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, func
from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundActionPriority,
    OutboundIntegrationDeliveryAttempt,
    Workspace,
)
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy


@dataclass(frozen=True)
class IntegrationOperationalSummary:
    active_integration_account_count: int
    pending_outbound_action_count: int
    delivered_outbound_action_count: int
    failed_outbound_action_count: int
    retryable_failed_action_count: int
    cancelled_outbound_action_count: int
    expired_outbound_action_count: int
    most_recent_outbound_at: datetime | None
    recent_delivered_count: int
    recent_failed_count: int
    priority_counts: dict[OutboundActionPriority, int]
    owned_outbound_action_count: int
    unowned_outbound_action_count: int
    archived_outbound_action_count: int
    unarchived_outbound_action_count: int


class IntegrationOperationalSummaryService:
    def __init__(self, session: Session, retry_policy: OutboundDeliveryRetryPolicy) -> None:
        self.session = session
        self.retry_policy = retry_policy

    def summarize(
        self, workspace: Workspace, *, window_days: int, now: datetime
    ) -> IntegrationOperationalSummary:
        cutoff = now - timedelta(days=window_days)
        active_account_count = self.session.exec(
            select(func.count()).select_from(IntegrationAccount).where(
                IntegrationAccount.workspace_id == workspace.id,
                IntegrationAccount.active.is_(True),
            )
        ).one()
        aggregate = self.session.exec(
            select(
                func.max(OutboundIntegrationAction.created_at),
                *[
                    func.sum(case((condition, 1), else_=0))
                    for condition in (
                        OutboundIntegrationAction.status == OutboundIntegrationActionStatus.PENDING,
                        OutboundIntegrationAction.status == OutboundIntegrationActionStatus.DELIVERED,
                        OutboundIntegrationAction.status == OutboundIntegrationActionStatus.FAILED,
                        OutboundIntegrationAction.status == OutboundIntegrationActionStatus.CANCELLED,
                        OutboundIntegrationAction.status == OutboundIntegrationActionStatus.EXPIRED,
                        (OutboundIntegrationAction.status == OutboundIntegrationActionStatus.DELIVERED)
                        & (OutboundIntegrationAction.created_at >= cutoff),
                        (OutboundIntegrationAction.status == OutboundIntegrationActionStatus.FAILED)
                        & (OutboundIntegrationAction.created_at >= cutoff),
                        OutboundIntegrationAction.priority == OutboundActionPriority.LOW,
                        OutboundIntegrationAction.priority == OutboundActionPriority.NORMAL,
                        OutboundIntegrationAction.priority == OutboundActionPriority.HIGH,
                        OutboundIntegrationAction.priority == OutboundActionPriority.URGENT,
                        OutboundIntegrationAction.owner_reference.is_not(None),
                        OutboundIntegrationAction.owner_reference.is_(None),
                        OutboundIntegrationAction.archived_at.is_not(None),
                        OutboundIntegrationAction.archived_at.is_(None),
                    )
                ],
            ).where(OutboundIntegrationAction.workspace_id == workspace.id)
        ).one()
        failed_actions = list(
            self.session.exec(
                select(OutboundIntegrationAction).where(
                    OutboundIntegrationAction.workspace_id == workspace.id,
                    OutboundIntegrationAction.status == OutboundIntegrationActionStatus.FAILED,
                )
            ).all()
        )
        attempt_counts = dict(
            self.session.exec(
                select(
                    OutboundIntegrationDeliveryAttempt.outbound_integration_action_id,
                    func.max(OutboundIntegrationDeliveryAttempt.attempt_number),
                )
                .where(
                    OutboundIntegrationDeliveryAttempt.workspace_id == workspace.id,
                    OutboundIntegrationDeliveryAttempt.outbound_integration_action_id.in_(
                        [action.id for action in failed_actions] or [None]
                    ),
                )
                .group_by(OutboundIntegrationDeliveryAttempt.outbound_integration_action_id)
            ).all()
        )
        retryable = sum(
            self.retry_policy.evaluate(
                attempt_count=attempt_counts.get(action.id, 0) or 0,
                failure_code=action.failure_code,
                failure_classification=action.failure_classification,
            ).allowed
            for action in failed_actions
        )
        values = [value or 0 for value in aggregate[1:]]
        return IntegrationOperationalSummary(
            active_integration_account_count=active_account_count or 0,
            pending_outbound_action_count=values[0],
            delivered_outbound_action_count=values[1],
            failed_outbound_action_count=values[2],
            retryable_failed_action_count=retryable,
            cancelled_outbound_action_count=values[3],
            expired_outbound_action_count=values[4],
            most_recent_outbound_at=aggregate[0],
            recent_delivered_count=values[5],
            recent_failed_count=values[6],
            priority_counts={
                OutboundActionPriority.LOW: values[7],
                OutboundActionPriority.NORMAL: values[8],
                OutboundActionPriority.HIGH: values[9],
                OutboundActionPriority.URGENT: values[10],
            },
            owned_outbound_action_count=values[11],
            unowned_outbound_action_count=values[12],
            archived_outbound_action_count=values[13],
            unarchived_outbound_action_count=values[14],
        )
