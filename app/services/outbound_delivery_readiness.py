"""Deterministic, read-only readiness evaluation for outbound delivery."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlmodel import Session

from app.models import OutboundIntegrationActionStatus, Workspace
from app.services.delivery_adapters import DeliveryAdapterRegistry, default_delivery_adapter_registry
from app.services.outbound_delivery import OutboundIntegrationDeliveryService
from app.services.outbound_delivery_approvals import OutboundDeliveryApprovalService
from app.services.outbound_delivery_status import OutboundIntegrationDeliveryStatusService
from app.services.outbound_retry_delay_policy import OutboundDeliveryRetryDelayPolicy
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy


@dataclass(frozen=True)
class OutboundDeliveryReadiness:
    action_id: UUID
    status: OutboundIntegrationActionStatus
    ready: bool
    blocking_reasons: tuple[str, ...]
    next_retry_at: datetime | None


class OutboundDeliveryReadinessService:
    """Compose existing delivery policies without invoking delivery adapters."""

    def __init__(
        self,
        session: Session,
        *,
        retry_policy: OutboundDeliveryRetryPolicy,
        retry_delay_policy: OutboundDeliveryRetryDelayPolicy,
        adapter_registry: DeliveryAdapterRegistry | None = None,
    ) -> None:
        self.status_service = OutboundIntegrationDeliveryStatusService(
            session, retry_policy, retry_delay_policy
        )
        self.approval_service = OutboundDeliveryApprovalService(session)
        self.adapter_registry = adapter_registry or default_delivery_adapter_registry()

    def evaluate(
        self, workspace: Workspace, account_id: UUID, action_id: UUID
    ) -> OutboundDeliveryReadiness:
        status_view = self.status_service.get_status_for_action(
            workspace, account_id, action_id
        )
        action = status_view.action
        account = status_view.account
        reasons: list[str] = []
        candidate = False

        if not account.active:
            reasons.append("integration_account_inactive")
        if action.status == OutboundIntegrationActionStatus.PENDING:
            candidate = True
        elif action.status == OutboundIntegrationActionStatus.FAILED:
            if status_view.retry_eligibility.allowed:
                candidate = True
            else:
                reasons.append(
                    f"retry_{status_view.retry_eligibility.denial_reason or 'not_allowed'}"
                )
        else:
            reasons.append(f"action_{action.status}")

        if candidate and OutboundIntegrationDeliveryService.is_expired(action):
            reasons.append("action_expired")
        if candidate and OutboundIntegrationDeliveryService.is_before_not_before(action):
            reasons.append("not_before_not_reached")
        if candidate:
            approval = self.approval_service.evaluate(workspace, action)
            if not approval.allowed:
                reasons.append(approval.denial_reason or "approval_not_approved")
            capability_failure = self.adapter_registry.validate_action(
                account.provider, action
            )
            if capability_failure is not None:
                reasons.append(capability_failure.failure_code or "adapter_not_ready")

        return OutboundDeliveryReadiness(
            action_id=action.id,
            status=action.status,
            ready=not reasons,
            blocking_reasons=tuple(reasons),
            next_retry_at=status_view.next_retry_at,
        )
