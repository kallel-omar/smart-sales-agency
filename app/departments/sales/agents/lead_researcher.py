from urllib.parse import urlparse

from app.core.agent_skill_execution import AgentSkillExecutionContext
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.prompt_composition import (
    SALES_DEPARTMENT_POLICY,
    SALES_PLATFORM_POLICY,
    PromptComposition,
    PromptCompositionInput,
    SalesPromptComposer,
    SalesSkillInstruction,
    UntrustedPromptContext,
    WorkspaceSalesInstructions,
)
from app.departments.sales.research_qualification_expertise import (
    ACCOUNT_RESEARCH_INSTRUCTIONS,
    ACCOUNT_RESEARCH_KEY,
    BUYING_SIGNAL_DETECTION_KEY,
    RESEARCH_QUALIFICATION_VERSION,
    AccountResearchInput,
    AccountResearchOutput,
    ExpertiseExecutionResult,
    ResearchQualificationContractError,
    ResearchQualificationValidationError,
    account_execution_result,
    account_research_components,
    build_account_research_input,
    buying_signal_components,
    buying_signal_execution_result,
    detect_buying_signals,
    persisted_research_evidence,
    safe_account_research_output,
)
from app.departments.sales.skills import sales_agent_skill_registry
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

    async def run(
        self,
        lead: Lead,
        *,
        agent_skill_execution_contexts: tuple[AgentSkillExecutionContext, ...] | None = None,
    ) -> dict:
        if agent_skill_execution_contexts is not None:
            return await self._run_governed(lead, agent_skill_execution_contexts)

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
                    attribution=self.context.ai_execution_attribution,
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
            "lead_id": str(lead.id),
            "lead_research_id": str(research.id),
            "summary": research.summary,
            "pain_points": research.pain_points,
            "opportunities": research.opportunities,
            "evidence": research.evidence,
        }

    async def _run_governed(
        self,
        lead: Lead,
        contexts: tuple[AgentSkillExecutionContext, ...],
    ) -> dict:
        if self.context.workspace is None:
            raise RuntimeError("A server-resolved workspace is required for lead research")
        account_context, buying_context = self._validated_contexts(contexts)
        source = build_account_research_input(
            self.context.workspace.id,
            lead,
            self.context.repository.conversation_history(lead.id),
            self.context.workspace.sales_instructions,
        )
        account, account_output = await self._execute_account_research(
            lead,
            source,
            account_context,
        )
        buying_definition = sales_agent_skill_registry().resolve(
            BUYING_SIGNAL_DETECTION_KEY,
            RESEARCH_QUALIFICATION_VERSION,
        )
        buying_components = buying_signal_components(buying_definition)
        if not isinstance(source, buying_components.input_contract):
            raise TypeError("Buying signal input contract is invalid")
        buying_output = detect_buying_signals(source)
        buying_output = buying_components.validator.validate(buying_output, source)
        if not isinstance(buying_output, buying_components.output_contract):
            raise TypeError("Buying signal output contract is invalid")
        buying = buying_signal_execution_result(buying_output)

        research = self.context.repository.save_research(
            lead=lead,
            summary=str(account.structured_result["company_summary"]),
            pain_points=[
                str(item["claim"])
                for item in account.structured_result["potential_needs"]
                if isinstance(item, dict) and isinstance(item.get("claim"), str)
            ],
            opportunities=[
                "Validate the lead's needs through discovery",
                "Prepare a personalized discovery message",
            ],
            evidence=persisted_research_evidence(account_output, buying_output, source),
        )
        return {
            "lead_id": str(lead.id),
            "lead_research_id": str(research.id),
            "summary": research.summary,
            "pain_points": research.pain_points,
            "opportunities": research.opportunities,
            "evidence": research.evidence,
            "agent_skills": [
                self._skill_metadata(account_context, account),
                self._skill_metadata(buying_context, buying),
            ],
        }

    async def _execute_account_research(
        self,
        lead: Lead,
        source: AccountResearchInput,
        context: AgentSkillExecutionContext,
    ) -> tuple[ExpertiseExecutionResult, AccountResearchOutput]:
        definition = sales_agent_skill_registry().resolve(
            ACCOUNT_RESEARCH_KEY,
            RESEARCH_QUALIFICATION_VERSION,
        )
        components = account_research_components(definition)
        if not isinstance(source, components.input_contract):
            raise TypeError("Account research input contract is invalid")
        if self.context.settings.llm_mode == "demo":
            output = safe_account_research_output(source)
            validated = components.validator.validate(output, source)
            if not isinstance(validated, components.output_contract):
                raise RuntimeError("Account research output contract is invalid")
            return account_execution_result(validated, ai_invoked=False), validated

        if self.context.ai_invocation_gateway is None:
            raise RuntimeError("No AI invocation gateway is configured for lead research")
        rendered = self._compose_governed_prompt(lead, source).render()
        invocation = await self.context.ai_invocation_gateway.invoke(
            AIInvocationGatewayRequest(
                workspace=self.context.workspace,
                task=AIModelRoutingTask.SIMPLE_SUMMARY,
                task_identifier=context.attribution_identifier,
                agent_identifier="lead_research",
                system_prompt=rendered.system_prompt,
                user_prompt=rendered.user_prompt,
                conversation_id=lead.id,
                attribution=context.ai_execution_attribution,
            )
        )
        try:
            if invocation.content is None:
                raise ResearchQualificationContractError("Account research output is missing")
            generated = AccountResearchOutput.from_json(invocation.content)
            validated = components.validator.validate(generated, source)
            if not isinstance(validated, components.output_contract):
                raise ResearchQualificationContractError(
                    "Account research output contract is invalid"
                )
        except (ResearchQualificationContractError, ResearchQualificationValidationError):
            fallback = safe_account_research_output(source)
            return (
                account_execution_result(fallback, ai_invoked=True, rejected=True),
                fallback,
            )
        return account_execution_result(validated, ai_invoked=True), validated

    def _compose_governed_prompt(
        self,
        lead: Lead,
        source: AccountResearchInput,
    ) -> PromptComposition:
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
                    "You are a cautious B2B lead research agent. Use only bounded HIRI "
                    "context and never perform external research."
                ),
                skill_instruction=SalesSkillInstruction(
                    identifier="sales.account_research.instruction.v1",
                    content=ACCOUNT_RESEARCH_INSTRUCTIONS,
                ),
                workspace_instructions=workspace_instructions,
                untrusted_context=(
                    UntrustedPromptContext(
                        label="Authoritative lead and conversation evidence",
                        content=source.render(),
                    ),
                ),
                current_task=(
                    "Return the account_research:v1 JSON contract for the supplied evidence."
                ),
            )
        )

    @staticmethod
    def _validated_contexts(
        contexts: tuple[AgentSkillExecutionContext, ...],
    ) -> tuple[AgentSkillExecutionContext, AgentSkillExecutionContext]:
        if len(contexts) != 2:
            raise RuntimeError("Research WorkItem requires exactly two governed skills")
        account, buying = contexts
        expected = (
            (ACCOUNT_RESEARCH_KEY, RESEARCH_QUALIFICATION_VERSION),
            (BUYING_SIGNAL_DETECTION_KEY, RESEARCH_QUALIFICATION_VERSION),
        )
        actual = (
            (account.skill_key, account.skill_version),
            (buying.skill_key, buying.skill_version),
        )
        if actual != expected or account.effective_tool_ceiling or buying.effective_tool_ceiling:
            raise RuntimeError("Research AgentSkill execution context is invalid")
        return account, buying

    @staticmethod
    def _skill_metadata(
        context: AgentSkillExecutionContext,
        result: ExpertiseExecutionResult,
    ) -> dict[str, object]:
        return {
            "key": context.skill_key,
            "version": context.skill_version,
            "outcome": result.outcome.value,
            "validation_outcome": result.validation_outcome.value,
            "result": result.structured_result,
        }
