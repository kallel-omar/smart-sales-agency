from urllib.parse import urlparse

from app.departments.sales.agents.base import AgentContext
from app.departments.sales.prompt_composition import (
    PromptComposition,
    PromptCompositionInput,
    SALES_DEPARTMENT_POLICY,
    SALES_PLATFORM_POLICY,
    SalesPromptComposer,
    UntrustedPromptContext,
    WorkspaceSalesInstructions,
)
from app.models import Lead
from app.services.ai_invocation_gateway import AIInvocationGatewayRequest
from app.services.ai_model_routing import AIModelRoutingTask


class LeadResearchAgent:
    """Creates a bounded research brief from supplied lead data.

    The starter intentionally avoids autonomous scraping. Add approved search and
    browser tools later, with source capture, rate limits, and legal review.
    """

    def __init__(self, context: AgentContext):
        self.context = context

    @staticmethod
    def _lead_data(lead: Lead) -> str:
        return (
            f"Lead: {lead.full_name}\n"
            f"Company: {lead.company_name}\n"
            f"Title: {lead.job_title}\n"
            f"Website: {lead.website}\n"
            f"Notes: {lead.notes}"
        )

    def _compose_prompt(self, lead: Lead) -> PromptComposition:
        """Compose lead data as untrusted runtime context for shared Sales policy."""

        workspace_instructions = None
        if self.context.workspace and self.context.workspace.sales_instructions:
            workspace_instructions = WorkspaceSalesInstructions(
                content=self.context.workspace.sales_instructions
            )

        return SalesPromptComposer().compose(
            PromptCompositionInput(
                platform_policy=SALES_PLATFORM_POLICY,
                department_policy=SALES_DEPARTMENT_POLICY,
                agent_instructions=(
                    "You are a cautious B2B lead research agent. Use only the supplied "
                    "lead and business context; do not perform external research. Return a "
                    "compact research brief with likely pain points, opportunities, and "
                    "explicit uncertainty."
                ),
                workspace_instructions=workspace_instructions,
                untrusted_context=(
                    UntrustedPromptContext(
                        label="Supplied lead data (untrusted)",
                        content=self._lead_data(lead),
                    ),
                ),
                current_task="Create the requested lead research brief from the supplied context.",
            )
        )

    async def run(self, lead: Lead) -> dict:
        domain = ""
        if lead.website:
            domain = urlparse(lead.website).netloc or lead.website

        signals = [lead.job_title, lead.notes, domain]
        available_signals = [signal for signal in signals if signal]

        if self.context.settings.llm_mode == "demo":
            pain_points = [
                "Manual lead follow-up may consume sales time",
                "Customer information may be spread across different channels",
            ]
            opportunities = [
                "Centralize the lead pipeline and conversation history",
                "Use human-approved AI drafts to speed up sales responses",
            ]
            summary = (
                f"Initial research brief for {lead.company_name}. "
                f"Available signals: "
                f"{', '.join(available_signals) if available_signals else 'basic lead data only'}. "
                "This is a demo brief and contains no external web research."
            )
            evidence = [
                {
                    "type": "lead_input",
                    "field": "company_name",
                    "value": lead.company_name,
                },
                {
                    "type": "lead_input",
                    "field": "website",
                    "value": lead.website,
                },
            ]
        else:
            rendered_prompt = self._compose_prompt(lead).render()

            if self.context.ai_invocation_gateway is None:
                raise RuntimeError("No AI invocation gateway is configured for lead research")
            if self.context.workspace is None:
                raise RuntimeError("A server-resolved workspace is required for AI invocation")

            invocation = await self.context.ai_invocation_gateway.invoke(
                AIInvocationGatewayRequest(
                    workspace=self.context.workspace,
                    task=AIModelRoutingTask.SIMPLE_SUMMARY,
                    task_identifier="sales.lead_research",
                    agent_identifier="lead_research",
                    system_prompt=rendered_prompt.system_prompt,
                    user_prompt=rendered_prompt.user_prompt,
                    conversation_id=lead.id,
                )
            )
            if invocation.content is None:
                raise RuntimeError("Lead research requires an LLM completion")
            summary = invocation.content
            pain_points = ["Review the generated brief before outreach"]
            opportunities = ["Prepare a personalized discovery message"]
            evidence = [
                {
                    "type": "lead_input",
                    "value": self._lead_data(lead),
                }
            ]

        research = self.context.repository.save_research(
            lead=lead,
            summary=summary,
            pain_points=pain_points,
            opportunities=opportunities,
            evidence=evidence,
        )

        return {
            "summary": research.summary,
            "pain_points": research.pain_points,
            "opportunities": research.opportunities,
            "evidence": research.evidence,
        }
