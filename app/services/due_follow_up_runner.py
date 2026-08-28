"""Bounded production runner for persisted due Sales follow-ups."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.departments.sales.follow_up_expertise import (
    FollowUpStopReason,
    terminal_follow_up_stop_reason,
)
from app.models import FollowUpTask, Lead, LeadStatus, Workspace
from app.observability import log_structured_event
from app.services.follow_up_work_items import (
    FollowUpWorkItemConfigurationError,
    FollowUpWorkItemMaterializationService,
    FollowUpWorkItemScopeError,
)
from app.services.repository import SalesRepository

DEFAULT_DUE_FOLLOW_UP_LIMIT = 100
MAX_DUE_FOLLOW_UP_LIMIT = 500

due_follow_up_logger = logging.getLogger("app.due_follow_up_runner")


@dataclass(frozen=True, slots=True)
class DueFollowUpRunSummary:
    scanned: int
    eligible: int
    materialized: int
    reused: int
    skipped: int
    failed: int
    reason_counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "scanned": self.scanned,
            "eligible": self.eligible,
            "materialized": self.materialized,
            "reused": self.reused,
            "skipped": self.skipped,
            "failed": self.failed,
            "reason_counts": dict(sorted(self.reason_counts.items())),
        }


class DueFollowUpRunner:
    """Find due tasks and reuse the existing governed WorkItem materializer."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.materializer = FollowUpWorkItemMaterializationService(session)
        self.sales_repository = SalesRepository(session)

    def run(
        self,
        *,
        now: datetime | None = None,
        workspace_id: UUID | None = None,
        limit: int = DEFAULT_DUE_FOLLOW_UP_LIMIT,
    ) -> DueFollowUpRunSummary:
        if not 1 <= limit <= MAX_DUE_FOLLOW_UP_LIMIT:
            raise ValueError(
                f"Due follow-up limit must be between 1 and {MAX_DUE_FOLLOW_UP_LIMIT}"
            )
        run_now = _as_utc(now or datetime.now(UTC))
        # HIRI persists application datetimes in UTC using SQLAlchemy's timezone-naive
        # DateTime columns. Query with the equivalent naive UTC boundary, then pass the
        # aware value to domain services for deterministic Python comparisons.
        query_now = run_now.replace(tzinfo=None)

        log_structured_event(
            due_follow_up_logger,
            "due_follow_up_run_started",
            workspace_id=str(workspace_id) if workspace_id is not None else None,
            limit=limit,
        )
        rows = self._due_rows(query_now=query_now, workspace_id=workspace_id, limit=limit)
        eligible = 0
        materialized = 0
        reused = 0
        skipped = 0
        failed = 0
        reasons: Counter[str] = Counter()

        for task, lead, workspace in rows:
            terminal_reason = terminal_follow_up_stop_reason(LeadStatus(lead.status))
            if terminal_reason is not None:
                skipped += 1
                reasons[terminal_reason.value] += 1
                continue
            if self.sales_repository.get_sales_handoff(workspace, lead.id) is not None:
                skipped += 1
                reasons[FollowUpStopReason.ACTIVE_HUMAN_HANDOFF.value] += 1
                continue

            eligible += 1
            try:
                work_item, decision = self.materializer.materialize_due(
                    workspace,
                    task.id,
                    now=run_now,
                )
            except SQLAlchemyError:
                self.session.rollback()
                log_structured_event(
                    due_follow_up_logger,
                    "due_follow_up_run_aborted",
                    task_id=str(task.id),
                    workspace_id=str(workspace.id),
                    reason="database_error",
                )
                raise
            except FollowUpWorkItemScopeError:
                self.session.rollback()
                failed += 1
                reasons["scope_error"] += 1
                self._log_task_failure(task.id, workspace.id, "scope_error")
                continue
            except FollowUpWorkItemConfigurationError:
                self.session.rollback()
                failed += 1
                reasons["configuration_error"] += 1
                self._log_task_failure(task.id, workspace.id, "configuration_error")
                continue
            except Exception:  # noqa: BLE001 - one malformed task must not abort the batch.
                self.session.rollback()
                failed += 1
                reasons["task_processing_error"] += 1
                self._log_task_failure(task.id, workspace.id, "task_processing_error")
                continue

            if work_item is None:
                skipped += 1
                reasons["task_no_longer_eligible"] += 1
            elif decision is None:
                reused += 1
                reasons["existing_work_item_reused"] += 1
            else:
                materialized += 1
                reasons["work_item_materialized"] += 1

        summary = DueFollowUpRunSummary(
            scanned=len(rows),
            eligible=eligible,
            materialized=materialized,
            reused=reused,
            skipped=skipped,
            failed=failed,
            reason_counts=dict(reasons),
        )
        log_structured_event(
            due_follow_up_logger,
            "due_follow_up_run_completed",
            workspace_id=str(workspace_id) if workspace_id is not None else None,
            **summary.as_dict(),
        )
        return summary

    def _due_rows(
        self,
        *,
        query_now: datetime,
        workspace_id: UUID | None,
        limit: int,
    ) -> list[tuple[FollowUpTask, Lead, Workspace]]:
        statement = (
            select(FollowUpTask, Lead, Workspace)
            .join(Lead, FollowUpTask.lead_id == Lead.id)
            .join(Workspace, Lead.tenant_id == Workspace.slug)
            .where(
                FollowUpTask.status == "pending",
                FollowUpTask.due_at <= query_now,
                Workspace.active.is_(True),
            )
        )
        if workspace_id is not None:
            statement = statement.where(Workspace.id == workspace_id)
        return list(
            self.session.exec(
                statement.order_by(FollowUpTask.due_at.asc(), FollowUpTask.id.asc()).limit(limit)
            ).all()
        )

    @staticmethod
    def _log_task_failure(task_id: UUID, workspace_id: UUID, reason: str) -> None:
        log_structured_event(
            due_follow_up_logger,
            "due_follow_up_task_failed",
            task_id=str(task_id),
            workspace_id=str(workspace_id),
            reason=reason,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
