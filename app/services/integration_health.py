"""Read-only, persisted-state health summaries for integration accounts."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    Workspace,
)
from app.services.integration_accounts import IntegrationAccountService


@dataclass(frozen=True)
class IntegrationAccountHealthView:
    account: IntegrationAccount
    health: str
    most_recent_outbound_at: datetime | None
    recent_delivered_count: int
    recent_failed_count: int
    pending_action_count: int
    failed_action_count: int


class IntegrationHealthService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.account_service = IntegrationAccountService(session)

    def get_for_account(
        self, workspace: Workspace, account_id: UUID, *, window_days: int, now: datetime
    ) -> IntegrationAccountHealthView:
        account = self.account_service.get_for_workspace(workspace, account_id)
        cutoff = now - timedelta(days=window_days)
        base = [
            OutboundIntegrationAction.workspace_id == workspace.id,
            OutboundIntegrationAction.integration_account_id == account.id,
        ]
        most_recent_outbound_at = self.session.exec(
            select(func.max(OutboundIntegrationAction.created_at)).where(*base)
        ).one()
        recent_delivered_count = self._count(
            *base,
            OutboundIntegrationAction.status == OutboundIntegrationActionStatus.DELIVERED,
            OutboundIntegrationAction.created_at >= cutoff,
        )
        recent_failed_count = self._count(
            *base,
            OutboundIntegrationAction.status == OutboundIntegrationActionStatus.FAILED,
            OutboundIntegrationAction.created_at >= cutoff,
        )
        pending_action_count = self._count(
            *base, OutboundIntegrationAction.status == OutboundIntegrationActionStatus.PENDING
        )
        failed_action_count = self._count(
            *base, OutboundIntegrationAction.status == OutboundIntegrationActionStatus.FAILED
        )
        return IntegrationAccountHealthView(
            account=account,
            health=self._classify(
                account.active,
                recent_delivered_count,
                recent_failed_count,
                pending_action_count,
                failed_action_count,
            ),
            most_recent_outbound_at=most_recent_outbound_at,
            recent_delivered_count=recent_delivered_count,
            recent_failed_count=recent_failed_count,
            pending_action_count=pending_action_count,
            failed_action_count=failed_action_count,
        )

    def _count(self, *filters) -> int:
        return self.session.exec(select(func.count()).select_from(OutboundIntegrationAction).where(*filters)).one()

    @staticmethod
    def _classify(active: bool, recent_delivered: int, recent_failed: int, pending: int, failed: int) -> str:
        if not active:
            return "inactive"
        if recent_failed and not recent_delivered:
            return "degraded"
        if pending or failed:
            return "attention"
        return "healthy"
