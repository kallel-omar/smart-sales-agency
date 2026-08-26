"""Read-only, persisted-state health summaries for integration accounts."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.integrations.providers import get_provider_requirements
from app.models import (
    IntegrationAccount,
    IntegrationAccountConnectionStatus,
    IntegrationCredentialReference,
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
    credential_references_ready: bool
    credential_expired: bool


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
        references = list(
            self.session.exec(
                select(IntegrationCredentialReference).where(
                    IntegrationCredentialReference.workspace_id == workspace.id,
                    IntegrationCredentialReference.integration_account_id == account.id,
                )
            ).all()
        )
        requirements = get_provider_requirements(
            account.provider,
            account.provider_auth_mode,
        )
        purposes = {reference.purpose for reference in references}
        credential_references_ready = bool(
            requirements is not None
            and requirements.required_credential_purposes.issubset(purposes)
        )
        credential_expired = any(
            self._is_expired(reference.expires_at, now) for reference in references
        )
        return IntegrationAccountHealthView(
            account=account,
            health=self._classify(
                account,
                credential_references_ready,
                credential_expired,
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
            credential_references_ready=credential_references_ready,
            credential_expired=credential_expired,
        )

    def _count(self, *filters) -> int:
        return self.session.exec(select(func.count()).select_from(OutboundIntegrationAction).where(*filters)).one()

    @staticmethod
    def _classify(
        account: IntegrationAccount,
        credential_references_ready: bool,
        credential_expired: bool,
        recent_delivered: int,
        recent_failed: int,
        pending: int,
        failed: int,
    ) -> str:
        if account.connection_status == IntegrationAccountConnectionStatus.DISCONNECTED:
            return "disconnected"
        if account.connection_status == IntegrationAccountConnectionStatus.RECONNECT_REQUIRED:
            return "reconnect_required"
        if account.connection_status == IntegrationAccountConnectionStatus.CONFIGURED:
            return "setup_required"
        if not account.active:
            return "inactive"
        if credential_expired:
            return "credential_expired"
        if not credential_references_ready:
            return "attention"
        if recent_failed and not recent_delivered:
            return "degraded"
        if pending or failed:
            return "attention"
        return "healthy"

    @staticmethod
    def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
        if expires_at is None:
            return False
        comparison_now = now
        if expires_at.tzinfo is None:
            comparison_now = now.replace(tzinfo=None)
        return expires_at <= comparison_now
