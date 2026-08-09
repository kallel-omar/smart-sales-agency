import pytest

from app.models import OutboundIntegrationAction, OutboundIntegrationActionStatus
from app.services.outbound_action_state_transitions import (
    OutboundIntegrationActionInvalidStateTransitionError,
    OutboundIntegrationActionStateTransitionGuard,
)


def _action(status: OutboundIntegrationActionStatus) -> OutboundIntegrationAction:
    return OutboundIntegrationAction(
        workspace_id="00000000-0000-0000-0000-000000000001",
        integration_account_id="00000000-0000-0000-0000-000000000002",
        external_target_id="recipient",
        action_type="send_message",
        content="safe test content",
        idempotency_key="state-transition-test",
        status=status,
    )


def test_transition_guard_allows_current_delivery_retry_and_cancellation_paths():
    guard = OutboundIntegrationActionStateTransitionGuard()

    assert guard.can_transition(
        OutboundIntegrationActionStatus.PENDING, OutboundIntegrationActionStatus.CANCELLED
    )
    assert guard.can_transition(
        OutboundIntegrationActionStatus.PENDING, OutboundIntegrationActionStatus.DELIVERED
    )
    assert guard.can_transition(
        OutboundIntegrationActionStatus.PENDING, OutboundIntegrationActionStatus.FAILED
    )
    assert guard.can_transition(
        OutboundIntegrationActionStatus.FAILED, OutboundIntegrationActionStatus.DELIVERED
    )
    assert guard.can_transition(
        OutboundIntegrationActionStatus.FAILED, OutboundIntegrationActionStatus.FAILED
    )
    guard.require_pending_delivery(_action(OutboundIntegrationActionStatus.PENDING))
    guard.require_retry_attempt(_action(OutboundIntegrationActionStatus.FAILED))


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (OutboundIntegrationActionStatus.DELIVERED, OutboundIntegrationActionStatus.FAILED),
        (OutboundIntegrationActionStatus.CANCELLED, OutboundIntegrationActionStatus.DELIVERED),
        (OutboundIntegrationActionStatus.EXPIRED, OutboundIntegrationActionStatus.DELIVERED),
        (OutboundIntegrationActionStatus.PENDING, OutboundIntegrationActionStatus.PENDING),
    ],
)
def test_transition_guard_rejects_terminal_or_noop_state_changes(source, target):
    guard = OutboundIntegrationActionStateTransitionGuard()

    with pytest.raises(OutboundIntegrationActionInvalidStateTransitionError):
        guard.require_transition(_action(source), target)


def test_transition_guard_keeps_initial_delivery_and_retry_states_distinct():
    guard = OutboundIntegrationActionStateTransitionGuard()

    with pytest.raises(OutboundIntegrationActionInvalidStateTransitionError):
        guard.require_pending_delivery(_action(OutboundIntegrationActionStatus.FAILED))
    with pytest.raises(OutboundIntegrationActionInvalidStateTransitionError):
        guard.require_retry_attempt(_action(OutboundIntegrationActionStatus.PENDING))
