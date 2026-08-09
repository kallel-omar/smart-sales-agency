"""Stable, provider-neutral reasons for denied outbound state transitions."""

from enum import StrEnum

from app.models import OutboundIntegrationActionStatus


class OutboundActionTransitionReasonCode(StrEnum):
    """Machine-stable codes kept separate from human-readable explanations."""

    TRANSITION_NOOP = "transition_noop"
    TRANSITION_NOT_ALLOWED = "transition_not_allowed"
    ACTION_ALREADY_DELIVERED = "action_delivered"
    ACTION_CANCELLED = "action_cancelled"
    ACTION_EXPIRED = "action_expired"


_SAFE_MESSAGES: dict[OutboundActionTransitionReasonCode, str] = {
    OutboundActionTransitionReasonCode.TRANSITION_NOOP: (
        "The requested state is already the action's current state."
    ),
    OutboundActionTransitionReasonCode.TRANSITION_NOT_ALLOWED: (
        "The requested outbound action state transition is not allowed."
    ),
    OutboundActionTransitionReasonCode.ACTION_ALREADY_DELIVERED: (
        "The outbound action has already been delivered."
    ),
    OutboundActionTransitionReasonCode.ACTION_CANCELLED: "The outbound action was cancelled.",
    OutboundActionTransitionReasonCode.ACTION_EXPIRED: "The outbound action has expired.",
}


def transition_denial_reason(
    source: OutboundIntegrationActionStatus,
    target: OutboundIntegrationActionStatus,
) -> OutboundActionTransitionReasonCode:
    """Classify a guard-denied transition without reproducing guard rules."""
    if source == target:
        return OutboundActionTransitionReasonCode.TRANSITION_NOOP
    return {
        OutboundIntegrationActionStatus.DELIVERED: (
            OutboundActionTransitionReasonCode.ACTION_ALREADY_DELIVERED
        ),
        OutboundIntegrationActionStatus.CANCELLED: (
            OutboundActionTransitionReasonCode.ACTION_CANCELLED
        ),
        OutboundIntegrationActionStatus.EXPIRED: OutboundActionTransitionReasonCode.ACTION_EXPIRED,
    }.get(source, OutboundActionTransitionReasonCode.TRANSITION_NOT_ALLOWED)


def transition_reason_message(code: OutboundActionTransitionReasonCode) -> str:
    """Return the safe human-readable message for a stable transition reason."""
    return _SAFE_MESSAGES[code]
