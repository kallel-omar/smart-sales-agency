from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete
from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    IntegrationAccountAuditAction,
    IntegrationAccountAuditEvent,
    Workspace,
)

DEFAULT_AUDIT_EVENT_LIMIT = 50
MAX_AUDIT_EVENT_LIMIT = 100
DEFAULT_AUDIT_RETENTION_CLEANUP_BATCH_SIZE = 100


@dataclass(frozen=True)
class AuditRetentionCleanupResult:
    """Safe summary of one explicit audit-retention cleanup operation."""

    deleted_count: int
    cutoff: datetime


class AuditQueryValidationError(ValueError):
    """Raised when an audit query is outside the safe supported bounds."""


class IntegrationAccountAuditRetentionPolicy:
    """Computes retention cutoffs without deleting audit history.

    This policy is provider-neutral and intentionally has no database side
    effects. A future scheduled maintenance task can use its cutoff when an
    approved retention cleanup process is introduced.
    """

    def __init__(self, retention_days: int) -> None:
        if not 1 <= retention_days <= 3_650:
            raise ValueError("Audit retention days must be between 1 and 3650")
        self.retention_days = retention_days

    def cutoff(self, now: datetime) -> datetime:
        return now - timedelta(days=self.retention_days)


class IntegrationAccountAuditService:
    """Records and queries safe, workspace-scoped account lifecycle history."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        account: IntegrationAccount,
        action: IntegrationAccountAuditAction,
    ) -> IntegrationAccountAuditEvent:
        event = IntegrationAccountAuditEvent(
            workspace_id=account.workspace_id,
            integration_account_id=account.id,
            action=action,
        )
        self.session.add(event)
        return event

    def list_for_account(
        self,
        workspace: Workspace,
        account_id: UUID,
        *,
        action: IntegrationAccountAuditAction | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = DEFAULT_AUDIT_EVENT_LIMIT,
    ) -> list[IntegrationAccountAuditEvent]:
        return self._list(
            workspace,
            account_id=account_id,
            action=action,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )

    def list_for_workspace(
        self,
        workspace: Workspace,
        *,
        action: IntegrationAccountAuditAction | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = DEFAULT_AUDIT_EVENT_LIMIT,
    ) -> list[IntegrationAccountAuditEvent]:
        return self._list(
            workspace,
            action=action,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )

    def cleanup_for_workspace(
        self,
        workspace: Workspace,
        retention_policy: IntegrationAccountAuditRetentionPolicy,
        *,
        now: datetime,
        batch_size: int = DEFAULT_AUDIT_RETENTION_CLEANUP_BATCH_SIZE,
    ) -> AuditRetentionCleanupResult:
        """Explicitly delete this workspace's audit events older than retention.

        Events created exactly at the cutoff are retained. Each delete is
        selected and applied in a bounded batch, keeping the operation safe for
        large audit histories without affecting integration accounts.
        """
        if batch_size < 1:
            raise ValueError("Cleanup batch size must be at least 1")

        cutoff = retention_policy.cutoff(now)
        deleted_count = 0

        while True:
            event_ids = list(
                self.session.exec(
                    select(IntegrationAccountAuditEvent.id)
                    .where(
                        IntegrationAccountAuditEvent.workspace_id == workspace.id,
                        IntegrationAccountAuditEvent.created_at < cutoff,
                    )
                    .order_by(IntegrationAccountAuditEvent.created_at.asc())
                    .limit(batch_size)
                ).all()
            )
            if not event_ids:
                break

            result = self.session.exec(
                delete(IntegrationAccountAuditEvent).where(
                    IntegrationAccountAuditEvent.workspace_id == workspace.id,
                    IntegrationAccountAuditEvent.id.in_(event_ids),
                )
            )
            self.session.commit()
            deleted_count += result.rowcount or 0

        return AuditRetentionCleanupResult(
            deleted_count=deleted_count,
            cutoff=cutoff,
        )

    def _list(
        self,
        workspace: Workspace,
        *,
        account_id: UUID | None = None,
        action: IntegrationAccountAuditAction | None,
        created_after: datetime | None,
        created_before: datetime | None,
        limit: int,
    ) -> list[IntegrationAccountAuditEvent]:
        self._validate_query(created_after, created_before, limit)

        filters = [IntegrationAccountAuditEvent.workspace_id == workspace.id]
        if account_id is not None:
            filters.append(IntegrationAccountAuditEvent.integration_account_id == account_id)
        if action is not None:
            filters.append(IntegrationAccountAuditEvent.action == action)
        if created_after is not None:
            filters.append(IntegrationAccountAuditEvent.created_at >= created_after)
        if created_before is not None:
            filters.append(IntegrationAccountAuditEvent.created_at <= created_before)

        statement = (
            select(IntegrationAccountAuditEvent)
            .where(*filters)
            .order_by(
                IntegrationAccountAuditEvent.created_at.desc(),
                IntegrationAccountAuditEvent.id.desc(),
            )
            .limit(limit)
        )
        return list(self.session.exec(statement).all())

    @staticmethod
    def _validate_query(
        created_after: datetime | None,
        created_before: datetime | None,
        limit: int,
    ) -> None:
        if not 1 <= limit <= MAX_AUDIT_EVENT_LIMIT:
            raise AuditQueryValidationError(
                f"Audit limit must be between 1 and {MAX_AUDIT_EVENT_LIMIT}"
            )
        if (
            created_after is not None
            and created_before is not None
            and created_after > created_before
        ):
            raise AuditQueryValidationError(
                "created_after must not be later than created_before"
            )
