from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings
from app.core.lead_capture import LeadCaptureResult, LeadCaptureSignal
from app.departments.sales.services import (
    SalesConversationTurnInput,
    SalesConversationTurnResult,
    SalesConversationTurnService,
)
from app.models import InboundIntegrationEventReceipt, IntegrationAccount, Workspace
from app.schemas import InboundIntegrationEvent
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.lead_capture import LeadCaptureService
from app.services.repository import NotFoundError, SalesRepository


class InboundIntegrationService:
    """Normalizes trusted integration input into a Sales business event."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
    ) -> None:
        self.repository = SalesRepository(session)
        self.settings = settings

    def reserve_event(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        external_event_id: str,
    ) -> InboundEventReservation:
        """Durably reserve an authenticated provider event before dispatching it."""

        normalized_event_id = self._normalize_external_event_id(external_event_id)
        receipt = InboundIntegrationEventReceipt(
            workspace_id=workspace.id,
            integration_account_id=account.id,
            external_event_id=normalized_event_id,
        )
        self.repository.session.add(receipt)
        try:
            self.repository.session.flush()
            self.repository.session.commit()
            self.repository.session.refresh(receipt)
            return InboundEventReservation(receipt=receipt, first_delivery=True)
        except IntegrityError:
            self.repository.session.rollback()
            existing = self.repository.session.exec(
                select(InboundIntegrationEventReceipt).where(
                    InboundIntegrationEventReceipt.workspace_id == workspace.id,
                    InboundIntegrationEventReceipt.integration_account_id == account.id,
                    InboundIntegrationEventReceipt.external_event_id == normalized_event_id,
                )
            ).first()
            if existing is None:
                raise
            return InboundEventReservation(receipt=existing, first_delivery=False)

    def release_event_reservation(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        reservation: InboundEventReservation,
    ) -> None:
        """Release only this account's reserved event before dispatch has begun."""

        receipt = self.repository.session.exec(
            select(InboundIntegrationEventReceipt).where(
                InboundIntegrationEventReceipt.id == reservation.receipt.id,
                InboundIntegrationEventReceipt.workspace_id == workspace.id,
                InboundIntegrationEventReceipt.integration_account_id == account.id,
            )
        ).first()
        if receipt is None:
            return
        self.repository.session.delete(receipt)
        self.repository.session.commit()

    def capture_reserved_event(
        self,
        workspace: Workspace,
        account: IntegrationAccount,
        reservation: InboundEventReservation,
        signal: LeadCaptureSignal,
    ) -> LeadCaptureResult:
        """Capture before dispatch, releasing only this reservation on failure."""

        try:
            return LeadCaptureService(self.repository.session).capture(
                workspace.id,
                signal,
            )
        except Exception:
            self.release_event_reservation(workspace, account, reservation)
            raise

    async def handle_event(
        self,
        event: InboundIntegrationEvent,
        workspace: Workspace,
    ) -> SalesConversationTurnResult:
        """Handle a validated inbound event for its resolved workspace."""

        lead = self.repository.get_lead(event.lead_id)

        if lead.tenant_id != workspace.slug:
            raise NotFoundError("Lead not found")

        return await SalesConversationTurnService(
            repository=self.repository,
            settings=self.settings,
            workspace=workspace,
            ai_invocation_gateway=AIInvocationGateway(
                self.repository.session,
                self.settings,
            ),
        ).process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel=event.channel,
                customer_message=event.content,
            )
        )

    @staticmethod
    def _normalize_external_event_id(value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:=-]{0,199}", normalized):
            raise InboundIntegrationEventIdValidationError(
                "External event identifier is invalid"
            )
        return normalized


class InboundIntegrationEventIdValidationError(ValueError):
    """Raised when a provider event identifier is not safe to persist."""


@dataclass(frozen=True)
class InboundEventReservation:
    receipt: InboundIntegrationEventReceipt
    first_delivery: bool
