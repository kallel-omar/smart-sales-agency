from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    IntegrationAccountAuditAction,
    IntegrationAccountAuditEvent,
    Workspace,
)


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
    ) -> list[IntegrationAccountAuditEvent]:
        statement = (
            select(IntegrationAccountAuditEvent)
            .where(
                IntegrationAccountAuditEvent.workspace_id == workspace.id,
                IntegrationAccountAuditEvent.integration_account_id == account_id,
            )
            .order_by(IntegrationAccountAuditEvent.created_at.desc())
        )
        return list(self.session.exec(statement).all())
