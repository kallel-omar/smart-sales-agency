from urllib.parse import urlparse

from app.departments.sales.agents.base import AgentContext
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
            system = (
                "You are a cautious B2B lead research agent. Use only the supplied data. "
                "Do not invent facts. Return a compact research brief with likely pain points, "
                "opportunities, and explicit uncertainty."
            )
            user = (
                f"Lead: {lead.full_name}\n"
                f"Company: {lead.company_name}\n"
                f"Title: {lead.job_title}\n"
                f"Website: {lead.website}\n"
                f"Notes: {lead.notes}"
            )

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
                    system_prompt=system,
                    user_prompt=user,
                    conversation_id=lead.id,
                )
            )
            if invocation.content is None:
                raise RuntimeError("Lead research requires an LLM completion")
            summary = invocation.content
            pain_points = ["Review the generated brief before outreach"]
            opportunities = ["Prepare a personalized discovery message"]
            evidence = [{"type": "lead_input", "value": user}]

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
