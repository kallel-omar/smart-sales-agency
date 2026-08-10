from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.event_types import EventType
from app.core.events import BusinessEvent
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.handoff_policy import SalesHandoffSignals
from app.departments.sales.services.conversation_turn_service import (
    SalesConversationTurnInput,
    SalesConversationTurnService,
)
from app.departments.sales.supervisor import (
    SalesDepartmentSupervisor,
    SalesEvent,
)
from app.departments.sales.workflows import NewLeadWorkflow
from app.models import Lead, SalesHandoffReasonCode, SalesStage


@dataclass(slots=True, frozen=True)
class SalesReplyResult:
    """Result returned by a Sales Department conversation operation."""

    detected_stage: SalesStage
    draft_reply: str
    approval_id: UUID | None
    handoff_required: bool = False
    handoff_reason_code: SalesHandoffReasonCode | None = None


class SalesDepartmentService:
    """
    Application boundary for Sales Department operations.

    API routes and business-event handlers call this service instead of
    constructing workflows or specialist agents directly.
    """

    def __init__(self, context: AgentContext):
        self.context = context
        self.supervisor = SalesDepartmentSupervisor()

    async def run_new_lead_workflow(
        self,
        lead_id: UUID,
    ) -> dict:
        route = self.supervisor.route(
            SalesEvent.NEW_LEAD
        )

        if route != "research_and_qualify":
            raise RuntimeError(
                f"Unexpected Sales route for new lead: {route}"
            )

        workflow = NewLeadWorkflow(self.context)

        return await workflow.run(lead_id)

    async def draft_sales_reply(
        self,
        *,
        lead: Lead,
        channel: str,
        content: str,
        handoff_signals: SalesHandoffSignals | None = None,
    ) -> SalesReplyResult:
        """
        Process an inbound customer message through the Sales Department.

        The department supervisor selects the operation. The specialist
        conversation agent generates the reply, while deterministic
        application logic handles persistence and approval requirements.
        """

        route = self.supervisor.route(
            SalesEvent.INBOUND_MESSAGE
        )

        if route != "sales_conversation":
            raise RuntimeError(
                f"Unexpected Sales route for inbound message: {route}"
            )

        if self.context.workspace is None:
            raise RuntimeError("A server-resolved workspace is required for a sales conversation turn")
        result = await SalesConversationTurnService(
            repository=self.context.repository,
            settings=self.context.settings,
            workspace=self.context.workspace,
            ai_invocation_gateway=self.context.ai_invocation_gateway,
        ).process(
            SalesConversationTurnInput(
                lead_id=lead.id,
                channel=channel,
                customer_message=content,
                handoff_signals=handoff_signals,
            )
        )

        return SalesReplyResult(
            detected_stage=result.detected_stage,
            draft_reply=result.draft_reply,
            approval_id=result.approval_id,
            handoff_required=result.handoff_required,
            handoff_reason_code=result.handoff_reason_code,
        )

    async def handle_event(
        self,
        event: BusinessEvent,
    ) -> dict | SalesReplyResult:
        """
        Handle a business event owned by the Sales Department.

        External departments and platform services do not need to know
        which internal Sales workflow will execute the event.
        """

        if event.event_type == EventType.LEAD_GENERATED.value:
            lead_id_value = event.payload.get("lead_id")

            if not lead_id_value:
                raise ValueError(
                    "lead.generated event requires payload.lead_id"
                )

            try:
                lead_id = UUID(str(lead_id_value))
            except ValueError as exc:
                raise ValueError(
                    "lead.generated event contains an invalid lead_id"
                ) from exc

            return await self.run_new_lead_workflow(
                lead_id
            )

        if event.event_type == EventType.SALES_INBOUND_MESSAGE.value:
            lead_id_value = event.payload.get("lead_id")

            if not lead_id_value:
                raise ValueError(
                    "sales.inbound_message event requires payload.lead_id"
                )

            try:
                lead_id = UUID(str(lead_id_value))
            except ValueError as exc:
                raise ValueError(
                    "sales.inbound_message event contains an invalid lead_id"
                ) from exc

            lead = self.context.repository.get_lead(lead_id)

            return await self.draft_sales_reply(
                lead=lead,
                channel=str(event.payload["channel"]),
                content=str(event.payload["content"]),
            )

        raise ValueError(
            f"Unsupported Sales Department event: {event.event_type}"
        )
