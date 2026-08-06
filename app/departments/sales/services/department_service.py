from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.departments.sales.agents.base import AgentContext
from app.departments.sales.agents.sales_agent import SalesConversationAgent
from app.departments.sales.supervisor import (
    SalesDepartmentSupervisor,
    SalesEvent,
)
from app.departments.sales.workflows import NewLeadWorkflow
from app.models import ConversationMessage, Lead, SalesStage


@dataclass(slots=True, frozen=True)
class SalesReplyResult:
    """Result returned by a Sales Department conversation operation."""

    detected_stage: SalesStage
    draft_reply: str
    approval_id: UUID | None


class SalesDepartmentService:
    """
    Application boundary for Sales Department operations.

    API routes call this service instead of constructing workflows
    or specialist agents directly.
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

        agent = SalesConversationAgent(
            self.context
        )

        stage, reply = await agent.draft_reply(
            lead,
            content,
        )

        self.context.repository.add_message(
            ConversationMessage(
                lead_id=lead.id,
                direction="inbound",
                channel=channel,
                stage=stage,
                content=content,
            )
        )

        approval_id: UUID | None = None

        if self.context.settings.require_human_approval:
            approval = self.context.repository.create_approval(
                lead_id=lead.id,
                channel=channel,
                payload={
                    "recipient": (
                        lead.email
                        or lead.phone
                        or lead.full_name
                    ),
                    "content": reply,
                    "stage": stage.value,
                },
            )

            approval_id = approval.id

        else:
            self.context.repository.add_message(
                ConversationMessage(
                    lead_id=lead.id,
                    direction="outbound",
                    channel=channel,
                    stage=stage,
                    content=reply,
                )
            )

        return SalesReplyResult(
            detected_stage=stage,
            draft_reply=reply,
            approval_id=approval_id,
        )