"""Provider-neutral delivery-status callback persistence for outbound actions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from sqlmodel import Session, select

from app.models import (
    IntegrationAccount,
    OutboundDeliveryFailureClassification,
    OutboundIntegrationAction,
    OutboundProviderDeliveryStatusEvent,
    ProviderDeliveryStatus,
    Workspace,
)
from app.services.integration_accounts import IntegrationAccountService
from app.services.outbound_delivery import OutboundIntegrationActionNotFoundError

DEFAULT_PROVIDER_DELIVERY_STATUS_EVENT_LIMIT = 100
MAX_PROVIDER_DELIVERY_STATUS_EVENT_LIMIT = 500


class ProviderDeliveryStatusEventValidationError(ValueError):
    """Raised when a provider status event query is outside safe bounds."""


class ProviderDeliveryStatusEventActionNotFoundError(LookupError):
    """Raised when a status callback cannot be correlated in the scoped account."""


@dataclass(frozen=True)
class ProviderDeliveryStatusEventRecordResult:
    event: OutboundProviderDeliveryStatusEvent
    duplicate: bool


class OutboundProviderDeliveryStatusEventService:
    """Record safe provider callback history without changing action state."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.account_service = IntegrationAccountService(session)

    def record_event(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        *,
        provider_delivery_id: str,
        provider_status: ProviderDeliveryStatus,
        provider_timestamp: datetime | None = None,
        provider_error_code: str | None = None,
        provider_error_title: str | None = None,
        provider_error_type: str | None = None,
        failure_classification: OutboundDeliveryFailureClassification | None = None,
    ) -> ProviderDeliveryStatusEventRecordResult:
        action = self._get_action_by_provider_delivery_id(
            workspace,
            account,
            provider_delivery_id,
        )
        normalized_status = ProviderDeliveryStatus(provider_status)
        normalized_timestamp = self._normalize_timestamp(provider_timestamp)
        if normalized_status != ProviderDeliveryStatus.FAILED:
            provider_error_code = None
            provider_error_title = None
            provider_error_type = None
            failure_classification = None

        idempotency_key = self._idempotency_key(
            provider_delivery_id=provider_delivery_id,
            provider_status=normalized_status,
            provider_timestamp=normalized_timestamp,
            provider_error_code=provider_error_code,
            provider_error_title=provider_error_title,
            provider_error_type=provider_error_type,
            failure_classification=failure_classification,
        )
        existing = self.session.exec(
            select(OutboundProviderDeliveryStatusEvent).where(
                OutboundProviderDeliveryStatusEvent.workspace_id == workspace.id,
                OutboundProviderDeliveryStatusEvent.integration_account_id == account.id,
                OutboundProviderDeliveryStatusEvent.idempotency_key == idempotency_key,
            )
        ).first()
        if existing is not None:
            return ProviderDeliveryStatusEventRecordResult(existing, duplicate=True)

        event = OutboundProviderDeliveryStatusEvent(
            workspace_id=workspace.id,
            integration_account_id=account.id,
            outbound_integration_action_id=action.id,
            provider_delivery_id=provider_delivery_id,
            provider_status=normalized_status,
            provider_timestamp=normalized_timestamp,
            provider_error_code=provider_error_code,
            provider_error_title=provider_error_title,
            provider_error_type=provider_error_type,
            failure_classification=failure_classification,
            idempotency_key=idempotency_key,
        )
        self.session.add(event)
        self.session.commit()
        self.session.refresh(event)
        return ProviderDeliveryStatusEventRecordResult(event, duplicate=False)

    def list_for_action(
        self,
        workspace: Workspace,
        account_id: UUID,
        action_id: UUID,
        *,
        limit: int = DEFAULT_PROVIDER_DELIVERY_STATUS_EVENT_LIMIT,
    ) -> list[OutboundProviderDeliveryStatusEvent]:
        if not 1 <= limit <= MAX_PROVIDER_DELIVERY_STATUS_EVENT_LIMIT:
            raise ProviderDeliveryStatusEventValidationError(
                "Provider delivery status event limit must be between 1 and "
                f"{MAX_PROVIDER_DELIVERY_STATUS_EVENT_LIMIT}"
            )
        account = self.account_service.get_for_workspace(workspace, account_id)
        self._get_action_for_account(workspace, account, action_id)
        return list(
            self.session.exec(
                select(OutboundProviderDeliveryStatusEvent)
                .where(
                    OutboundProviderDeliveryStatusEvent.workspace_id == workspace.id,
                    OutboundProviderDeliveryStatusEvent.integration_account_id == account.id,
                    OutboundProviderDeliveryStatusEvent.outbound_integration_action_id == action_id,
                )
                .order_by(
                    OutboundProviderDeliveryStatusEvent.provider_timestamp.is_(None),
                    OutboundProviderDeliveryStatusEvent.provider_timestamp,
                    OutboundProviderDeliveryStatusEvent.created_at,
                    OutboundProviderDeliveryStatusEvent.id,
                )
                .limit(limit)
            ).all()
        )

    def _get_action_by_provider_delivery_id(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        provider_delivery_id: str,
    ) -> OutboundIntegrationAction:
        action = self.session.exec(
            select(OutboundIntegrationAction)
            .where(
                OutboundIntegrationAction.workspace_id == workspace.id,
                OutboundIntegrationAction.integration_account_id == account.id,
                OutboundIntegrationAction.provider_delivery_id == provider_delivery_id,
            )
            .order_by(OutboundIntegrationAction.created_at.desc())
        ).first()
        if action is None:
            raise ProviderDeliveryStatusEventActionNotFoundError(
                "Outbound integration action not found for provider delivery id"
            )
        return action

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
        if action is None:
            raise OutboundIntegrationActionNotFoundError("Outbound integration action not found")
        return action

    @staticmethod
    def _normalize_timestamp(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _idempotency_key(
        *,
        provider_delivery_id: str,
        provider_status: ProviderDeliveryStatus,
        provider_timestamp: datetime | None,
        provider_error_code: str | None,
        provider_error_title: str | None,
        provider_error_type: str | None,
        failure_classification: OutboundDeliveryFailureClassification | None,
    ) -> str:
        fingerprint = {
            "provider_delivery_id": provider_delivery_id,
            "provider_status": provider_status.value,
            "provider_timestamp": (provider_timestamp.isoformat() if provider_timestamp else None),
            "provider_error_code": provider_error_code,
            "provider_error_title": provider_error_title,
            "provider_error_type": provider_error_type,
            "failure_classification": (
                failure_classification.value if failure_classification else None
            ),
        }
        encoded = json.dumps(
            fingerprint,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()
