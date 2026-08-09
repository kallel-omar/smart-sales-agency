"""Read-only provider-neutral status summaries for outbound delivery actions."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    OutboundIntegrationAction,
    OutboundIntegrationDeliveryAttempt,
    Workspace,
)
from app.services.integration_accounts import IntegrationAccountService
from app.services.outbound_delivery import OutboundIntegrationActionNotFoundError
from app.services.outbound_retry_policy import (
    OutboundDeliveryRetryEligibility,
    OutboundDeliveryRetryPolicy,
)


@dataclass(frozen=True)
class OutboundIntegrationDeliveryStatusView:
    """Safe operational status data assembled without delivery-side effects."""

    action: OutboundIntegrationAction
    account: IntegrationAccount
    attempt_count: int
    retry_eligibility: OutboundDeliveryRetryEligibility


class OutboundIntegrationDeliveryStatusService:
    """Load one workspace-scoped action status without invoking an adapter."""

    def __init__(
        self,
        session: Session,
        retry_policy: OutboundDeliveryRetryPolicy,
    ) -> None:
        self.session = session
        self.account_service = IntegrationAccountService(session)
        self.retry_policy = retry_policy

    def get_status_for_action(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
    ) -> OutboundIntegrationDeliveryStatusView:
        account = self.account_service.get_for_workspace(workspace, account_id)
        action = self._get_action_for_account(workspace, account, action_id)
        attempt_count = self._attempt_count(workspace, account, action)
        retry_eligibility = self.retry_policy.evaluate_action(
            action_status=action.status,
            attempt_count=attempt_count,
            failure_code=action.failure_code,
        )
        return OutboundIntegrationDeliveryStatusView(
            action=action,
            account=account,
            attempt_count=attempt_count,
            retry_eligibility=retry_eligibility,
        )

    def _get_action_for_account(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        action_id: UUID,
    ) -> OutboundIntegrationAction:
        action = self.session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.id == action_id,
                OutboundIntegrationAction.workspace_id == workspace.id,
                OutboundIntegrationAction.integration_account_id == account.id,
            )
        ).first()
        if not action:
            raise OutboundIntegrationActionNotFoundError("Outbound integration action not found")
        return action

    def _attempt_count(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        action: OutboundIntegrationAction,
    ) -> int:
        count = self.session.exec(
            select(func.count()).where(
                OutboundIntegrationDeliveryAttempt.workspace_id == workspace.id,
                OutboundIntegrationDeliveryAttempt.integration_account_id == account.id,
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == action.id,
            )
        ).one()
        return int(count)
