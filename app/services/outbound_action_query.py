"""Workspace-scoped, read-only queries for outbound delivery intents."""

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    Workspace,
)

DEFAULT_OUTBOUND_ACTION_LIMIT = 50
MAX_OUTBOUND_ACTION_LIMIT = 100


class OutboundActionQueryValidationError(ValueError):
    """Raised when an outbound-action read filter has an invalid range."""


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
