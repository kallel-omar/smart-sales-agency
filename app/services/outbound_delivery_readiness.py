"""Deterministic, read-only readiness evaluation for outbound delivery."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlmodel import Session

from app.models import OutboundIntegrationAction, OutboundIntegrationActionStatus, Workspace
from app.services.delivery_adapters import DeliveryAdapterRegistry, default_delivery_adapter_registry
from app.services.outbound_delivery import OutboundIntegrationDeliveryService
from app.services.outbound_delivery_approvals import OutboundDeliveryApprovalService
from app.services.outbound_delivery_status import OutboundIntegrationDeliveryStatusService
from app.services.outbound_delivery_readiness_reasons import OutboundDeliveryReadinessReasonCode
from app.services.outbound_retry_delay_policy import OutboundDeliveryRetryDelayPolicy
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy


@dataclass(frozen=True)
class OutboundDeliveryReadiness:
    action_id: UUID
    status: OutboundIntegrationActionStatus
    ready: bool
    blocking_reasons: tuple[OutboundDeliveryReadinessReasonCode, ...]
    next_retry_at: datetime | None
    blocking_reason_details: tuple["OutboundDeliveryReadinessExplanation", ...]


@dataclass(frozen=True)
class OutboundDeliveryReadinessExplanation:
    """Safe explanation attached to one stable readiness blocking code."""

    code: OutboundDeliveryReadinessReasonCode
    message: str
    not_before: datetime | None = None
    expires_at: datetime | None = None
    next_retry_at: datetime | None = None


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
        reasons: list[OutboundDeliveryReadinessReasonCode] = []
        candidate = False

        if not account.active:
            reasons.append(OutboundDeliveryReadinessReasonCode.INTEGRATION_ACCOUNT_INACTIVE)
        if action.status == OutboundIntegrationActionStatus.PENDING:
            candidate = True
        elif action.status == OutboundIntegrationActionStatus.FAILED:
            if status_view.retry_eligibility.allowed:
                candidate = True
            else:
                reasons.append(OutboundDeliveryReadinessReasonCode.RETRY_NOT_ELIGIBLE)
        else:
            if action.status == OutboundIntegrationActionStatus.CANCELLED:
                reasons.append(OutboundDeliveryReadinessReasonCode.ACTION_CANCELLED)
            elif action.status == OutboundIntegrationActionStatus.EXPIRED:
                reasons.append(OutboundDeliveryReadinessReasonCode.ACTION_EXPIRED)
            elif action.status == OutboundIntegrationActionStatus.DELIVERED:
                reasons.append(OutboundDeliveryReadinessReasonCode.ACTION_ALREADY_DELIVERED)
            else:
                reasons.append(OutboundDeliveryReadinessReasonCode.ACTION_TERMINAL)

        if candidate and OutboundIntegrationDeliveryService.is_expired(action):
            reasons.append(OutboundDeliveryReadinessReasonCode.ACTION_EXPIRED)
        if candidate and OutboundIntegrationDeliveryService.is_before_not_before(action):
            reasons.append(OutboundDeliveryReadinessReasonCode.NOT_BEFORE_NOT_REACHED)
        if candidate:
            approval = self.approval_service.evaluate(workspace, action)
            if not approval.allowed:
                reasons.append(self._approval_reason(approval.denial_reason))
            capability_failure = self.adapter_registry.validate_action(
                account.provider, action
            )
            if capability_failure is not None:
                reasons.append(OutboundDeliveryReadinessReasonCode.ADAPTER_CAPABILITY_MISMATCH)

        reason_details = tuple(
            self._explanation_for(action, reason, status_view.next_retry_at) for reason in reasons
        )
        return OutboundDeliveryReadiness(
            action_id=action.id,
            status=action.status,
            ready=not reasons,
            blocking_reasons=tuple(reasons),
            next_retry_at=status_view.next_retry_at,
            blocking_reason_details=reason_details,
        )

    @staticmethod
    def _approval_reason(denial_reason: str | None) -> OutboundDeliveryReadinessReasonCode:
        """Translate established approval outcomes into the readiness registry."""
        return {
            "approval_unavailable": OutboundDeliveryReadinessReasonCode.APPROVAL_UNAVAILABLE,
            "approval_pending": OutboundDeliveryReadinessReasonCode.APPROVAL_PENDING,
            "approval_rejected": OutboundDeliveryReadinessReasonCode.APPROVAL_REJECTED,
        }.get(
            denial_reason,
            OutboundDeliveryReadinessReasonCode.APPROVAL_NOT_APPROVED,
        )

    @staticmethod
    def _explanation_for(
        action: OutboundIntegrationAction,
        code: OutboundDeliveryReadinessReasonCode,
        next_retry_at: datetime | None,
    ) -> OutboundDeliveryReadinessExplanation:
        """Add only the timestamp that explains a timing-based blocking reason."""
        from app.services.outbound_delivery_readiness_reasons import readiness_reason_message

        return OutboundDeliveryReadinessExplanation(
            code=code,
            message=readiness_reason_message(code),
            not_before=(
                OutboundDeliveryReadinessService._as_utc(action.not_before)
                if code == OutboundDeliveryReadinessReasonCode.NOT_BEFORE_NOT_REACHED
                else None
            ),
            expires_at=(
                OutboundDeliveryReadinessService._as_utc(action.expires_at)
                if code == OutboundDeliveryReadinessReasonCode.ACTION_EXPIRED
                else None
            ),
            next_retry_at=(
                OutboundDeliveryReadinessService._as_utc(next_retry_at)
                if code == OutboundDeliveryReadinessReasonCode.RETRY_NOT_ELIGIBLE
                else None
            ),
        )

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
