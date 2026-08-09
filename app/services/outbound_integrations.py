from uuid import UUID
from datetime import datetime

from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationAuditAction,
    OutboundIntegrationActionType,
    Workspace,
)
from app.services.integration_accounts import IntegrationAccountService
from app.services.outbound_action_audit import OutboundIntegrationActionAuditService
from app.services.outbound_delivery_approvals import OutboundDeliveryApprovalService


class InactiveIntegrationAccountError(ValueError):
    """Raised when a delivery intent targets an inactive integration account."""


class OutboundIntegrationActionIdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused for a different action."""


class OutboundIntegrationService:
    """Creates provider-neutral outbound delivery intents without sending them."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.account_service = IntegrationAccountService(session)
        self.audit_service = OutboundIntegrationActionAuditService(session)

    def create_action(
        self,
        workspace: Workspace,
        account_id: UUID,
        *,
        external_target_id: str,
        action_type: OutboundIntegrationActionType,
        content: str,
        payload: dict,
        correlation_id: str | None,
        idempotency_key: str,
        expires_at: datetime | None = None,
        requires_approval: bool = False,
    ) -> tuple[OutboundIntegrationAction, IntegrationAccount]:
        account = self.account_service.get_for_workspace(workspace, account_id)
        if not account.active:
            raise InactiveIntegrationAccountError("Integration account is inactive")

        normalized_input = {
            "external_target_id": external_target_id.strip(),
            "action_type": action_type,
            "content": content,
            "payload": payload,
            "correlation_id": correlation_id.strip() if correlation_id else None,
            "expires_at": expires_at,
            "requires_approval": requires_approval,
        }
        normalized_key = idempotency_key.strip()
        existing = self.session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.workspace_id == workspace.id,
                OutboundIntegrationAction.integration_account_id == account.id,
                OutboundIntegrationAction.idempotency_key == normalized_key,
            )
        ).first()
        if existing:
            if not self._matches(existing, normalized_input):
                raise OutboundIntegrationActionIdempotencyConflictError(
                    "Idempotency key has already been used for a different action"
                )
            return existing, account

        action = OutboundIntegrationAction(
            workspace_id=workspace.id,
            integration_account_id=account.id,
            idempotency_key=normalized_key,
            **normalized_input,
        )
        self.session.add(action)
        self.session.flush()
        if requires_approval:
            OutboundDeliveryApprovalService(self.session).create_for_action(
                action, account.provider
            )
        self.audit_service.record(action, OutboundIntegrationAuditAction.CREATED)
        self.session.commit()
        self.session.refresh(action)
        return action, account

    @staticmethod
    def _matches(
        action: OutboundIntegrationAction,
        values: dict,
    ) -> bool:
        return (
            action.external_target_id == values["external_target_id"]
            and action.action_type == values["action_type"]
            and action.content == values["content"]
            and action.payload == values["payload"]
            and action.correlation_id == values["correlation_id"]
            and action.requires_approval == values["requires_approval"]
        )
