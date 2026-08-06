from __future__ import annotations

from uuid import UUID

from app.departments.sales.agents.base import AgentContext
from app.departments.sales.supervisor import (
    SalesDepartmentSupervisor,
    SalesEvent,
)
from app.departments.sales.workflows import NewLeadWorkflow


class SalesDepartmentService:
    """
    Application boundary for Sales Department operations.

    API routes should call this service instead of constructing workflows
    or specialist agents directly.

    The service delegates routing decisions to the Sales Department
    Supervisor and then executes the selected deterministic workflow.
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