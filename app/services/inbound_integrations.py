from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings
from app.core.event_factory import create_business_event
from app.core.event_payloads import InboundSalesMessagePayload
from app.core.event_types import EventType
from app.core.events import Department
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.services import SalesDepartmentService, SalesReplyResult
from app.models import IntegrationAccount, InboundIntegrationEventReceipt, Workspace
from app.schemas import InboundIntegrationEvent
from app.services.ai_invocation_gateway import AIInvocationGateway
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
    ) -> "InboundEventReservation":
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

    async def handle_event(
        self,
        event: InboundIntegrationEvent,
        workspace: Workspace,
    ) -> SalesReplyResult:
        """Handle a validated inbound event for its resolved workspace."""

        lead = self.repository.get_lead(event.lead_id)

        if lead.tenant_id != workspace.slug:
            raise NotFoundError("Lead not found")

        business_event = create_business_event(
            workspace_id=workspace.id,
            event_type=EventType.SALES_INBOUND_MESSAGE,
            source_department=Department.PLATFORM,
            destination_department=Department.SALES,
            payload=InboundSalesMessagePayload(
                lead_id=str(event.lead_id),
                channel=event.channel,
                content=event.content,
                external_event_id=event.external_event_id,
            ),
        )

        sales_department = SalesDepartmentService(
            AgentContext(
                settings=self.settings,
                repository=self.repository,
                llm=None,
                workspace=workspace,
                ai_invocation_gateway=AIInvocationGateway(
                    self.repository.session,
                    self.settings,
                ),
            )
        )

        result = await sales_department.handle_event(business_event)

        if not isinstance(result, SalesReplyResult):
            raise TypeError("Inbound event did not produce a sales reply")

        return result

    @staticmethod
    def _normalize_external_event_id(value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", normalized):
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
