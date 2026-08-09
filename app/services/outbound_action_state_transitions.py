"""Provider-neutral transition guard for outbound action lifecycle state."""

from typing import ClassVar

from app.models import OutboundIntegrationAction, OutboundIntegrationActionStatus


class OutboundIntegrationActionInvalidStateTransitionError(ValueError):
    """Raised when an outbound action state change is not part of its lifecycle."""


class OutboundIntegrationActionStateTransitionGuard:
    """Centralize valid action-state transitions without executing policy or I/O."""

    _ALLOWED_TARGETS: ClassVar[dict[
        OutboundIntegrationActionStatus, frozenset[OutboundIntegrationActionStatus]
    ]] = {
        OutboundIntegrationActionStatus.PENDING: frozenset(
            {
                OutboundIntegrationActionStatus.CANCELLED,
                OutboundIntegrationActionStatus.EXPIRED,
                OutboundIntegrationActionStatus.DELIVERED,
                OutboundIntegrationActionStatus.FAILED,
            }
        ),
        OutboundIntegrationActionStatus.FAILED: frozenset(
            {
                OutboundIntegrationActionStatus.DELIVERED,
                OutboundIntegrationActionStatus.FAILED,
            }
        ),
        OutboundIntegrationActionStatus.DELIVERED: frozenset(),
        OutboundIntegrationActionStatus.CANCELLED: frozenset(),
        OutboundIntegrationActionStatus.EXPIRED: frozenset(),
    }

    def can_transition(
        self,
        source: OutboundIntegrationActionStatus,
        target: OutboundIntegrationActionStatus,
    ) -> bool:
        return target in self._ALLOWED_TARGETS[source]

    def require_transition(
        self,
        action: OutboundIntegrationAction,
        target: OutboundIntegrationActionStatus,
    ) -> None:
        if self.can_transition(action.status, target):
            return
        raise OutboundIntegrationActionInvalidStateTransitionError(
            f"Outbound integration action cannot transition from {action.status} to {target}"
        )

    def require_pending_delivery(self, action: OutboundIntegrationAction) -> None:
        """Initial delivery is only valid while an action is pending."""
        if action.status == OutboundIntegrationActionStatus.PENDING:
            return
        raise OutboundIntegrationActionInvalidStateTransitionError(
            "Initial outbound delivery requires a pending action"
        )

    def require_retry_attempt(self, action: OutboundIntegrationAction) -> None:
        """A retry remains an explicit operation for a previously failed action."""
        if action.status == OutboundIntegrationActionStatus.FAILED:
            return
        raise OutboundIntegrationActionInvalidStateTransitionError(
            "Outbound delivery retry requires a failed action"
        )
