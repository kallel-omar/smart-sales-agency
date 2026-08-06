from uuid import UUID

from langgraph.graph import END, START, StateGraph

from app.departments.sales.agents.base import AgentContext
from app.departments.sales.agents.lead_researcher import LeadResearchAgent
from app.departments.sales.agents.qualifier import QualificationAgent
from app.departments.sales.workflows.state import SalesWorkflowState


class NewLeadWorkflow:
    """
    Sales Department workflow for a newly created lead.

    Flow:

    load lead
        ↓
    research
        ↓
    qualify
       / \
      /   \
qualified unqualified
    |         |
prepare     stop
outreach
    """

    def __init__(self, context: AgentContext):
        self.context = context

        self.research_agent = LeadResearchAgent(context)
        self.qualification_agent = QualificationAgent(context)

        self.graph = self._build()

    async def load_lead(
        self,
        state: SalesWorkflowState,
    ) -> dict:
        lead = self.context.repository.get_lead(
            state["lead_id"]
        )

        return {
            "lead": lead,
            "status": "lead_loaded",
        }

    async def research_lead(
        self,
        state: SalesWorkflowState,
    ) -> dict:
        research = await self.research_agent.run(
            state["lead"]
        )

        return {
            "research": research,
            "status": "researched",
        }

    async def qualify_lead(
        self,
        state: SalesWorkflowState,
    ) -> dict:
        result = await self.qualification_agent.run(
            state["lead"],
            state["research"],
        )

        return {
            "score": result.score,
            "qualified": result.qualified,
            "qualification_reasons": result.reasons,
            "status": (
                "qualified"
                if result.qualified
                else "unqualified"
            ),
        }

    async def prepare_outreach(
        self,
        state: SalesWorkflowState,
    ) -> dict:
        lead = state["lead"]
        research = state["research"]

        draft = (
            f"Hello {lead.full_name}, "
            f"I reviewed the information available about "
            f"{lead.company_name}. "
            f"{research['opportunities'][0]} "
            "Would it be useful to discuss your current "
            "sales process for 15 minutes?"
        )

        approval = self.context.repository.create_approval(
            lead_id=lead.id,
            channel=self.context.settings.default_channel,
            payload={
                "recipient": (
                    lead.email
                    or lead.phone
                    or lead.full_name
                ),
                "content": draft,
            },
        )

        return {
            "draft_message": draft,
            "approval_id": approval.id,
            "next_action": "human_approval",
            "status": "awaiting_approval",
        }

    async def stop_unqualified(
        self,
        state: SalesWorkflowState,
    ) -> dict:
        return {
            "draft_message": None,
            "approval_id": None,
            "next_action": (
                "collect_more_information_or_archive"
            ),
            "status": "unqualified",
        }

    def qualification_route(
        self,
        state: SalesWorkflowState,
    ) -> str:
        return (
            "qualified"
            if state.get("qualified")
            else "unqualified"
        )

    def _build(self):
        builder = StateGraph(SalesWorkflowState)

        builder.add_node(
            "load_lead",
            self.load_lead,
        )

        builder.add_node(
            "research",
            self.research_lead,
        )

        builder.add_node(
            "qualify",
            self.qualify_lead,
        )

        builder.add_node(
            "prepare_outreach",
            self.prepare_outreach,
        )

        builder.add_node(
            "stop_unqualified",
            self.stop_unqualified,
        )

        builder.add_edge(
            START,
            "load_lead",
        )

        builder.add_edge(
            "load_lead",
            "research",
        )

        builder.add_edge(
            "research",
            "qualify",
        )

        builder.add_conditional_edges(
            "qualify",
            self.qualification_route,
            {
                "qualified": "prepare_outreach",
                "unqualified": "stop_unqualified",
            },
        )

        builder.add_edge(
            "prepare_outreach",
            END,
        )

        builder.add_edge(
            "stop_unqualified",
            END,
        )

        return builder.compile()

    async def run(
        self,
        lead_id: UUID,
    ) -> SalesWorkflowState:
        return await self.graph.ainvoke(
            {
                "lead_id": lead_id,
            }
        )