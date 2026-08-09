from __future__ import annotations

from sqlmodel import Session

from app.config import Settings
from app.core.event_factory import create_business_event
from app.core.event_payloads import InboundSalesMessagePayload
from app.core.event_types import EventType
from app.core.events import Department
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.services import SalesDepartmentService, SalesReplyResult
from app.models import Workspace
from app.schemas import InboundIntegrationEvent
from app.services.llm import build_llm
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
                llm=build_llm(self.settings),
            )
        )

        result = await sales_department.handle_event(business_event)

        if not isinstance(result, SalesReplyResult):
            raise TypeError("Inbound event did not produce a sales reply")

        return result
