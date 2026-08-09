"""Stable, provider-neutral blocking reasons for outbound delivery readiness."""

from enum import StrEnum


class OutboundDeliveryReadinessReasonCode(StrEnum):
    """Machine-stable codes for deterministic readiness decisions."""

    INTEGRATION_ACCOUNT_INACTIVE = "integration_account_inactive"
    APPROVAL_UNAVAILABLE = "approval_unavailable"
    APPROVAL_PENDING = "approval_pending"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_NOT_APPROVED = "approval_not_approved"
    ACTION_CANCELLED = "action_cancelled"
    ACTION_EXPIRED = "action_expired"
    ACTION_ALREADY_DELIVERED = "action_delivered"
    ACTION_TERMINAL = "action_terminal"
    NOT_BEFORE_NOT_REACHED = "not_before_not_reached"
    ADAPTER_CAPABILITY_MISMATCH = "adapter_capability_mismatch"
    RETRY_NOT_ELIGIBLE = "retry_not_eligible"


_SAFE_MESSAGES: dict[OutboundDeliveryReadinessReasonCode, str] = {
    OutboundDeliveryReadinessReasonCode.INTEGRATION_ACCOUNT_INACTIVE: (
        "The integration account is inactive."
    ),
    OutboundDeliveryReadinessReasonCode.APPROVAL_UNAVAILABLE: (
        "The required delivery approval is unavailable."
    ),
    OutboundDeliveryReadinessReasonCode.APPROVAL_PENDING: (
        "The required delivery approval is still pending."
    ),
    OutboundDeliveryReadinessReasonCode.APPROVAL_REJECTED: (
        "The required delivery approval was rejected."
    ),
    OutboundDeliveryReadinessReasonCode.APPROVAL_NOT_APPROVED: (
        "The required delivery approval has not been approved."
    ),
    OutboundDeliveryReadinessReasonCode.ACTION_CANCELLED: "The outbound action was cancelled.",
    OutboundDeliveryReadinessReasonCode.ACTION_EXPIRED: "The outbound action has expired.",
    OutboundDeliveryReadinessReasonCode.ACTION_ALREADY_DELIVERED: (
        "The outbound action has already been delivered."
    ),
    OutboundDeliveryReadinessReasonCode.ACTION_TERMINAL: (
        "The outbound action is in a terminal state."
    ),
    OutboundDeliveryReadinessReasonCode.NOT_BEFORE_NOT_REACHED: (
        "The outbound action is not available yet."
    ),
    OutboundDeliveryReadinessReasonCode.ADAPTER_CAPABILITY_MISMATCH: (
        "The integration adapter cannot deliver this outbound action."
    ),
    OutboundDeliveryReadinessReasonCode.RETRY_NOT_ELIGIBLE: (
        "The failed outbound action is not eligible for retry."
    ),
}


def readiness_reason_message(code: OutboundDeliveryReadinessReasonCode) -> str:
    """Return the safe human-readable message for a stable reason code."""
    return _SAFE_MESSAGES[code]
