"""Read-only validation for proposed outbound action state changes."""

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session

from app.models import OutboundIntegrationActionStatus, Workspace
from app.services.integration_accounts import IntegrationAccountService
from app.services.outbound_action_state_transitions import (
    OutboundIntegrationActionInvalidStateTransitionError,
    OutboundIntegrationActionStateTransitionGuard,
)
from app.services.outbound_action_transition_reasons import (
    OutboundActionTransitionReasonCode,
    transition_denial_reason,
)
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


@dataclass(frozen=True)
class OutboundActionTransitionValidation:
    """Safe result of validating a requested action state change."""

    allowed: bool
    current_state: OutboundIntegrationActionStatus
    requested_target: OutboundIntegrationActionStatus
    denial_reason: OutboundActionTransitionReasonCode | None


class OutboundActionTransitionValidationService:
    """Validate through the shared guard without applying a state change."""

    def __init__(self, session: Session) -> None:
        self.account_service = IntegrationAccountService(session)
        self.delivery_service = OutboundIntegrationDeliveryService(session)
        self.transition_guard = OutboundIntegrationActionStateTransitionGuard()

    def validate(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
        requested_target: OutboundIntegrationActionStatus,
    ) -> OutboundActionTransitionValidation:
        account = self.account_service.get_for_workspace(workspace, account_id)
        action = self.delivery_service._get_action_for_account(workspace, account, action_id)
        try:
            self.transition_guard.require_transition(action, requested_target)
        except OutboundIntegrationActionInvalidStateTransitionError:
            return OutboundActionTransitionValidation(
                allowed=False,
                current_state=action.status,
                requested_target=requested_target,
                denial_reason=transition_denial_reason(action.status, requested_target),
            )
        return OutboundActionTransitionValidation(
            allowed=True,
            current_state=action.status,
            requested_target=requested_target,
            denial_reason=None,
        )
