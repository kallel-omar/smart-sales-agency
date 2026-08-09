"""Workspace-scoped, read-only queries for outbound delivery intents."""

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select
from sqlalchemy import delete

from app.models import (
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundActionPriority,
    Workspace,
)

DEFAULT_OUTBOUND_ACTION_LIMIT = 50
MAX_OUTBOUND_ACTION_LIMIT = 100


class OutboundActionQueryValidationError(ValueError):
    """Raised when an outbound-action read filter has an invalid range."""


class OutboundIntegrationActionQueryNotFoundError(LookupError):
    """Raised when a requested action is outside the current workspace."""


class OutboundIntegrationActionQueryService:
    """Return outbound actions only from the resolved current workspace."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_workspace(
        self,
        workspace: Workspace,
        *,
        action_status: OutboundIntegrationActionStatus | None = None,
        provider: str | None = None,
        integration_account_id: UUID | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = DEFAULT_OUTBOUND_ACTION_LIMIT,
    ) -> list[tuple[OutboundIntegrationAction, str]]:
        if created_after and created_before and created_after > created_before:
            raise OutboundActionQueryValidationError(
                "created_after must be earlier than or equal to created_before"
            )

        statement = (
            select(OutboundIntegrationAction, IntegrationAccount.provider)
            .join(
                IntegrationAccount,
                IntegrationAccount.id == OutboundIntegrationAction.integration_account_id,
            )
            .where(OutboundIntegrationAction.workspace_id == workspace.id)
        )
        if action_status:
            statement = statement.where(OutboundIntegrationAction.status == action_status)
        if provider:
            statement = statement.where(
                IntegrationAccount.provider == provider.strip()
            )
        if integration_account_id:
            statement = statement.where(
                OutboundIntegrationAction.integration_account_id == integration_account_id
            )
        if created_after:
            statement = statement.where(OutboundIntegrationAction.created_at >= created_after)
        if created_before:
            statement = statement.where(OutboundIntegrationAction.created_at <= created_before)
        return list(
            self.session.exec(
                statement.order_by(OutboundIntegrationAction.created_at.desc()).limit(limit)
            ).all()
        )

    def get_for_workspace(
        self,
        workspace: Workspace,
        action_id: UUID,
    ) -> tuple[OutboundIntegrationAction, str]:
        """Return one action joined to its account provider in the current workspace."""
        row = self.session.exec(
            select(OutboundIntegrationAction, IntegrationAccount.provider)
            .join(
                IntegrationAccount,
                IntegrationAccount.id == OutboundIntegrationAction.integration_account_id,
            )
            .where(
                OutboundIntegrationAction.id == action_id,
                OutboundIntegrationAction.workspace_id == workspace.id,
                IntegrationAccount.workspace_id == workspace.id,
            )
        ).first()
        if not row:
            raise OutboundIntegrationActionQueryNotFoundError(
                "Outbound integration action not found"
            )
        return row

    def set_priority(self, workspace: Workspace, action_id: UUID, priority: OutboundActionPriority) -> OutboundIntegrationAction:
        action, _ = self.get_for_workspace(workspace, action_id)
        action.priority = priority
        self.session.add(action)
        self.session.commit()
        self.session.refresh(action)
        return action

    def cleanup_expired_for_workspace(self, workspace: Workspace, cutoff: datetime) -> int:
        result = self.session.execute(
            delete(OutboundIntegrationAction).where(
                OutboundIntegrationAction.workspace_id == workspace.id,
                OutboundIntegrationAction.status.in_(("expired", "cancelled")),
                OutboundIntegrationAction.expires_at.is_not(None),
                OutboundIntegrationAction.expires_at < cutoff,
            )
        )
        self.session.commit()
        return result.rowcount or 0
