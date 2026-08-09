from app.models import OutboundIntegrationActionStatus
from app.services.outbound_action_transition_reasons import (
    OutboundActionTransitionReasonCode,
    transition_denial_reason,
    transition_reason_message,
)


def test_transition_denial_reasons_are_stable_and_have_safe_messages():
    assert transition_denial_reason(
        OutboundIntegrationActionStatus.PENDING, OutboundIntegrationActionStatus.PENDING
    ) == OutboundActionTransitionReasonCode.TRANSITION_NOOP
    assert transition_denial_reason(
        OutboundIntegrationActionStatus.FAILED, OutboundIntegrationActionStatus.CANCELLED
    ) == OutboundActionTransitionReasonCode.TRANSITION_NOT_ALLOWED
    assert transition_denial_reason(
        OutboundIntegrationActionStatus.DELIVERED, OutboundIntegrationActionStatus.FAILED
    ) == OutboundActionTransitionReasonCode.ACTION_ALREADY_DELIVERED
    assert transition_reason_message(OutboundActionTransitionReasonCode.ACTION_EXPIRED) == (
        "The outbound action has expired."
    )
