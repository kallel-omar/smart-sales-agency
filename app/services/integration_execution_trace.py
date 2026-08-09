"""Read-only workspace-scoped composition for correlated integration executions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    ApprovalRequest,
    InboundIntegrationEventReceipt,
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationDeliveryAttempt,
    Workspace,
)


class IntegrationExecutionTraceNotFoundError(LookupError):
    """Raised when a correlation is not visible in the requesting workspace."""


@dataclass(frozen=True)
class IntegrationExecutionInboundReceiptView:
    receipt: InboundIntegrationEventReceipt
    account: IntegrationAccount


@dataclass(frozen=True)
class IntegrationExecutionOutboundActionView:
    action: OutboundIntegrationAction
    account: IntegrationAccount
    approval: ApprovalRequest | None
    delivery_attempts: tuple[OutboundIntegrationDeliveryAttempt, ...]


@dataclass(frozen=True)
class IntegrationExecutionTraceView:
    receipt: IntegrationExecutionInboundReceiptView
    outbound_actions: tuple[IntegrationExecutionOutboundActionView, ...]


class IntegrationExecutionTraceService:
    """Compose persisted records without mutating integrations or invoking adapters."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_workspace(
        self,
        workspace: Workspace,
        correlation_id: UUID,
    ) -> IntegrationExecutionTraceView:
        receipt_row = self.session.exec(
            select(InboundIntegrationEventReceipt, IntegrationAccount)
            .join(
                IntegrationAccount,
                IntegrationAccount.id
                == InboundIntegrationEventReceipt.integration_account_id,
            )
            .where(
                InboundIntegrationEventReceipt.correlation_id == correlation_id,
                InboundIntegrationEventReceipt.workspace_id == workspace.id,
                IntegrationAccount.workspace_id == workspace.id,
            )
        ).first()
        if receipt_row is None:
            raise IntegrationExecutionTraceNotFoundError("Integration execution trace not found")

        receipt, receipt_account = receipt_row
        action_rows = self.session.exec(
            select(OutboundIntegrationAction, IntegrationAccount)
            .join(
                IntegrationAccount,
                IntegrationAccount.id == OutboundIntegrationAction.integration_account_id,
            )
            .where(
                OutboundIntegrationAction.workspace_id == workspace.id,
                IntegrationAccount.workspace_id == workspace.id,
                OutboundIntegrationAction.correlation_id == str(receipt.correlation_id),
            )
            .order_by(
                OutboundIntegrationAction.created_at.asc(),
                OutboundIntegrationAction.id.asc(),
            )
        ).all()

        outbound_actions = tuple(
            self._outbound_action_view(workspace, action, account)
            for action, account in action_rows
        )
        return IntegrationExecutionTraceView(
            receipt=IntegrationExecutionInboundReceiptView(receipt, receipt_account),
            outbound_actions=outbound_actions,
        )

    def _outbound_action_view(
        self,
        workspace: Workspace,
        action: OutboundIntegrationAction,
        account: IntegrationAccount,
    ) -> IntegrationExecutionOutboundActionView:
        approval = (
            self.session.get(ApprovalRequest, action.approval_request_id)
            if action.approval_request_id is not None
            else None
        )
        attempts = tuple(
            self.session.exec(
                select(OutboundIntegrationDeliveryAttempt)
                .where(
                    OutboundIntegrationDeliveryAttempt.workspace_id == workspace.id,
                    OutboundIntegrationDeliveryAttempt.integration_account_id == account.id,
                    OutboundIntegrationDeliveryAttempt.outbound_integration_action_id
                    == action.id,
                )
                .order_by(
                    OutboundIntegrationDeliveryAttempt.started_at.asc(),
                    OutboundIntegrationDeliveryAttempt.attempt_number.asc(),
                    OutboundIntegrationDeliveryAttempt.id.asc(),
                )
            ).all()
        )
        return IntegrationExecutionOutboundActionView(
            action=action,
            account=account,
            approval=approval,
            delivery_attempts=attempts,
        )
