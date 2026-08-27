import re

from app.core.agent_skill_execution import AgentSkillExecutionContext
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.conversation_expertise import (
    BUYER_INDECISION_KEY,
    CONVERSATION_EXPERTISE_VERSION,
    NEEDS_DISCOVERY_KEY,
    OBJECTION_HANDLING_KEY,
    BuyerIndecisionOutput,
    ConversationExpertiseContractError,
    ConversationExpertiseExecutionResult,
    ConversationExpertiseInput,
    ConversationExpertiseMessage,
    ConversationExpertiseValidationError,
    NeedsDiscoveryOutput,
    ObjectionHandlingOutput,
    ObjectionType,
    SalesEvidenceClassification,
    SalesEvidenceFact,
    accepted_conversation_expertise_result,
    conversation_expertise_components,
    objection_type_for,
    parse_conversation_expertise_output,
    safe_conversation_expertise_result,
    skill_instructions,
)
from app.departments.sales.language_policy import (
    render_sales_communication_instruction,
    select_sales_communication_style,
    select_sales_tone,
)
from app.departments.sales.pricing_explanation import (
    PRICING_EXPLANATION_INSTRUCTIONS,
    PRICING_EXPLANATION_KEY,
    PRICING_EXPLANATION_VERSION,
    PricingConversationMessage,
    PricingExplanationContractError,
    PricingExplanationExecutionResult,
    PricingExplanationInput,
    PricingExplanationOutput,
    PricingExplanationOutputValidator,
    PricingExplanationValidationError,
    PricingValidationOutcome,
    analyze_pricing_evidence,
    canonical_product_facts,
    commercial_exception_kind,
    preserve_code_switching,
    pricing_explanation_components,
    safe_pricing_result,
)
from app.departments.sales.prompt_composition import (
    SALES_COMMERCIAL_GROUNDING_POLICY,
    SALES_CONVERSATION_QUALITY_POLICY,
    SALES_CONVERSATION_STRATEGY_POLICY,
    SALES_DEPARTMENT_POLICY,
    SALES_HANDOFF_POLICY,
    SALES_PLATFORM_POLICY,
    PromptComposition,
    PromptCompositionInput,
    PromptMessage,
    PromptMessageRole,
    PromptTrustLevel,
    SalesBusinessContext,
    SalesLanguageToneInstruction,
    SalesProductContext,
    SalesPromptComposer,
    SalesSkillInstruction,
    WorkspaceSalesInstructions,
)
from app.departments.sales.qualification_collection import SalesQualificationContext
from app.departments.sales.skills import sales_agent_skill_registry
from app.models import ConversationMessage, Lead, Product, SalesStage
from app.services.ai_invocation_gateway import AIInvocationGatewayRequest
from app.services.ai_model_routing import AIModelRoutingTask


class SalesConversationAgent:
    def __init__(self, context: AgentContext):
        self.context = context

    def detect_stage(self, message: str) -> SalesStage:
        text = message.lower()
        if any(term in text for term in ("price", "cost", "how much", "Ø³Ø¹Ø±", "Ù‚Ø¯Ø§Ø´", "prix")):
            return SalesStage.QUALIFICATION
        if any(term in text for term in ("expensive", "too much", "ØºØ§Ù„ÙŠ", "cher", "not interested")):
            return SalesStage.OBJECTION_HANDLING
        if any(term in text for term in ("buy", "order", "start", "sign", "Ù†Ø­Ø¨ Ù†Ø§Ø®Ø°", "commander")):
            return SalesStage.CLOSING
        if any(term in text for term in ("need", "problem", "looking for", "Ù†Ø­ØªØ§Ø¬", "besoin")):
            return SalesStage.DISCOVERY
        return SalesStage.VALUE_PROPOSITION

    @staticmethod
    def _product_context(products: list[Product]) -> tuple[SalesProductContext, ...]:
        """Copy only authoritative product facts into transient prompt context."""

        product_context: list[SalesProductContext] = []
        for product in products[:10]:
            billing = product.metadata_json.get("billing")
            billing_period = billing.strip() if isinstance(billing, str) and billing.strip() else None
            product_context.append(
                SalesProductContext(
                    name=product.name,
                    description=product.description,
                    price=product.price,
                    billing_period=billing_period,
                    active=product.active,
                )
            )
        return tuple(product_context)

    def _conversation_context(
        self,
        history: list[ConversationMessage],
    ) -> tuple[PromptMessage, ...]:
        """Map persisted history to role-aware, untrusted conversation context."""

        messages: list[PromptMessage] = []
        for message in history:
            role = (
                PromptMessageRole.USER
                if message.direction == "inbound"
                else PromptMessageRole.ASSISTANT
            )
            messages.append(
                PromptMessage(
                    role=role,
                    content=message.content,
                    trust_level=PromptTrustLevel.UNTRUSTED,
                )
            )
        return tuple(messages)

    def _prior_customer_messages(
        self,
        history: list[ConversationMessage],
    ) -> tuple[str, ...]:
        return tuple(
            message.content
            for message in history
            if message.direction == "inbound"
        )

    def _compose_prompt(
        self,
        *,
        lead: Lead,
        inbound: str,
        stage: SalesStage,
        products: list[Product],
        conversation_history: list[ConversationMessage] | None = None,
        skill_instruction: SalesSkillInstruction | None = None,
        qualification_context: SalesQualificationContext | None = None,
    ) -> PromptComposition:
        """Build transient Sales context while keeping customer text untrusted."""

        history = (
            conversation_history
            if conversation_history is not None
            else self.context.repository.conversation_history(lead.id)
        )

        workspace_instructions = None
        if self.context.workspace and self.context.workspace.sales_instructions:
            workspace_instructions = WorkspaceSalesInstructions(
                content=self.context.workspace.sales_instructions
            )

        workspace = self.context.workspace
        communication_style = select_sales_communication_style(
            customer_message=inbound,
            workspace_preferred_language=(
                workspace.sales_preferred_language if workspace else None
            ),
            workspace_preferred_script=(
                workspace.sales_preferred_script if workspace else None
            ),
            prior_customer_messages=self._prior_customer_messages(history),
        )
        tone = select_sales_tone(
            workspace.sales_preferred_tone if workspace else None
        )

        return SalesPromptComposer().compose(
            PromptCompositionInput(
                platform_policy=SALES_PLATFORM_POLICY,
                department_policy=SALES_DEPARTMENT_POLICY,
                commercial_grounding_policy=SALES_COMMERCIAL_GROUNDING_POLICY,
                agent_instructions="Ask one useful next question.",
                sales_conversation_strategy_policy=SALES_CONVERSATION_STRATEGY_POLICY,
                sales_conversation_quality_policy=SALES_CONVERSATION_QUALITY_POLICY,
                sales_handoff_policy=SALES_HANDOFF_POLICY,
                language_tone_instruction=SalesLanguageToneInstruction(
                    content=render_sales_communication_instruction(
                        language=communication_style.language,
                        script=communication_style.script,
                        tone=tone,
                    )
                ),
                skill_instruction=skill_instruction,
                workspace_instructions=workspace_instructions,
                business_context=SalesBusinessContext(
                    company_name=self.context.workspace.name if self.context.workspace else None,
                    products=self._product_context(products),
                    qualification_context=qualification_context,
                ),
                conversation_messages=self._conversation_context(history),
                current_task=(
                    f"Sales stage: {stage.value}\nLead: {lead.full_name} at {lead.company_name}\n"
                    f"Customer message: {inbound}"
                ),
            )
        )

    async def draft_reply(
        self,
        lead: Lead,
        inbound: str,
        *,
        conversation_history: list[ConversationMessage] | None = None,
        current_stage: SalesStage | None = None,
        qualification_context: SalesQualificationContext | None = None,
    ) -> tuple[SalesStage, str]:
        stage = self.detect_stage(inbound)
        canonical_stage = current_stage or stage
        products = self.context.repository.list_products(lead.tenant_id)

        if self.context.settings.llm_mode == "demo":
            first_name = re.split(r"\s+", lead.full_name.strip())[0]

            if products and products[0].price is not None:
                product = products[0]

                billing = product.metadata_json.get("billing", "")
                billing_text = f" {billing}" if billing else ""

                pricing_reply = (
                    f"Thanks {first_name}. Our {product.name} costs "
                    f"{product.price:.2f}{billing_text}. "
                    "Would you like me to explain what is included?"
                )
            else:
                pricing_reply = (
                    f"Thanks {first_name}. Pricing depends on message volume, channels, "
                    "and the level of automation. What channels do you currently use?"
                )

            replies = {
                SalesStage.DISCOVERY: (
                    f"Thanks {first_name}. To recommend the right option, what is the main sales "
                    "problem you want to solve first: lead capture, faster replies, or follow-up?"
                ),
               SalesStage.QUALIFICATION: pricing_reply,
                SalesStage.OBJECTION_HANDLING: (
                    f"I understand, {first_name}. We should not add automation unless it saves more "
                    "time or revenue than it costs. Which part feels too expensive or unnecessary?"
                ),
                SalesStage.CLOSING: (
                    f"Great, {first_name}. I can prepare the next step, but a human team member will "
                    "confirm the scope, final price, and payment details before anything is activated."
                ),
                SalesStage.VALUE_PROPOSITION: (
                    f"Thanks {first_name}. Our sales assistant can organize leads, draft personalized "
                    "replies, and schedule follow-ups while keeping a human approval step before sending."
                ),
            }
            reply = replies[stage]
        else:
            history = (
                conversation_history
                if conversation_history is not None
                else self.context.repository.conversation_history(lead.id)
            )
            rendered_prompt = self._compose_prompt(
                lead=lead,
                inbound=inbound,
                stage=canonical_stage,
                products=products,
                conversation_history=history,
                qualification_context=qualification_context,
            ).render()
            if self.context.ai_invocation_gateway is None:
                raise RuntimeError("No AI invocation gateway is configured for sales conversation")
            if self.context.workspace is None:
                raise RuntimeError("A server-resolved workspace is required for AI invocation")
            invocation = await self.context.ai_invocation_gateway.invoke(
                AIInvocationGatewayRequest(
                    workspace=self.context.workspace,
                    task=AIModelRoutingTask.SALES_CONVERSATION,
                    task_identifier="sales.conversation.reply",
                    agent_identifier="sales_conversation",
                    system_prompt=rendered_prompt.system_prompt,
                    user_prompt=rendered_prompt.user_prompt,
                    conversation_id=lead.id,
                    sales_stage=canonical_stage,
                    attribution=self.context.ai_execution_attribution,
                )
            )
            if invocation.content is None:
                raise RuntimeError("Sales conversation requires an LLM completion")
            reply = invocation.content

        return stage, reply

    async def execute_pricing_explanation(
        self,
        lead: Lead,
        inbound: str,
        skill_context: AgentSkillExecutionContext,
        *,
        conversation_history: list[ConversationMessage] | None = None,
        current_stage: SalesStage | None = None,
        qualification_context: SalesQualificationContext | None = None,
    ) -> tuple[SalesStage, PricingExplanationExecutionResult]:
        """Execute the one authorized pricing Skill through the existing gateway."""

        if self.context.workspace is None:
            raise RuntimeError("A server-resolved workspace is required for AgentSkill execution")
        if (
            skill_context.workspace_id != self.context.workspace.id
            or skill_context.skill_key != PRICING_EXPLANATION_KEY
            or skill_context.skill_version != PRICING_EXPLANATION_VERSION
            or skill_context.effective_tool_ceiling
        ):
            raise PermissionError("Pricing AgentSkill execution context is not authorized")
        definition = sales_agent_skill_registry().resolve(
            PRICING_EXPLANATION_KEY,
            PRICING_EXPLANATION_VERSION,
        )
        if (
            skill_context.input_contract != definition.input_contract
            or skill_context.output_contract != definition.output_contract
            or skill_context.validator != definition.validator
            or skill_context.instruction_component != definition.instruction_component
            or skill_context.attribution_identifier != definition.attribution_identifier
        ):
            raise PermissionError("Pricing AgentSkill definition does not match its context")
        components = pricing_explanation_components(definition)
        if components.input_contract is not PricingExplanationInput:
            raise RuntimeError("Pricing AgentSkill input contract is not registered")
        if components.output_contract is not PricingExplanationOutput:
            raise RuntimeError("Pricing AgentSkill output contract is not registered")
        if not isinstance(components.validator, PricingExplanationOutputValidator):
            raise TypeError("Pricing AgentSkill validator is not registered")

        stage = self.detect_stage(inbound)
        canonical_stage = current_stage or stage
        history = (
            conversation_history
            if conversation_history is not None
            else self.context.repository.conversation_history(lead.id)
        )
        products = self.context.repository.list_products(lead.tenant_id)
        source = self._pricing_input(
            inbound=inbound,
            stage=canonical_stage,
            products=products,
            history=history,
        )
        deterministic_reasons = {
            "ambiguous_product",
            "multiple_named_products",
            "conflicting_pricing",
            "currency_unavailable",
            "product_fact_unavailable",
        }
        if (
            commercial_exception_kind(inbound) is not None
            or source.evidence_reason in deterministic_reasons
            or self.context.settings.llm_mode == "demo"
        ):
            return stage, safe_pricing_result(source)

        code_switching_instruction = (
            " Preserve the customer's natural French/Tunisian code-switching where practical."
            if source.preserve_code_switching
            else ""
        )
        rendered_prompt = self._compose_prompt(
            lead=lead,
            inbound=inbound,
            stage=canonical_stage,
            products=products,
            conversation_history=history,
            qualification_context=qualification_context,
            skill_instruction=SalesSkillInstruction(
                identifier=skill_context.instruction_component,
                content=PRICING_EXPLANATION_INSTRUCTIONS + code_switching_instruction,
            ),
        ).render()
        if self.context.ai_invocation_gateway is None:
            raise RuntimeError("No AI invocation gateway is configured for pricing AgentSkill")
        invocation = await self.context.ai_invocation_gateway.invoke(
            AIInvocationGatewayRequest(
                workspace=self.context.workspace,
                task=AIModelRoutingTask.CONTEXTUAL_CUSTOMER_RESPONSE,
                task_identifier=skill_context.attribution_identifier,
                agent_identifier="sales_conversation",
                system_prompt=rendered_prompt.system_prompt,
                user_prompt=rendered_prompt.user_prompt,
                conversation_id=lead.id,
                sales_stage=canonical_stage,
                attribution=skill_context.ai_execution_attribution,
            )
        )
        if invocation.content is None:
            raise RuntimeError("Pricing AgentSkill requires an LLM completion")
        try:
            output = PricingExplanationOutput.from_json(invocation.content)
            components.validator.validate(output, source)
        except (PricingExplanationContractError, PricingExplanationValidationError) as exc:
            fallback = safe_pricing_result(
                source,
                reason="generated_output_rejected",
                validation_rejected=True,
            )
            return stage, PricingExplanationExecutionResult(
                response_text=fallback.response_text,
                outcome=fallback.outcome,
                validation_outcome=fallback.validation_outcome,
                validation_reason=type(exc).__name__,
                ai_invoked=True,
                escalation_kind=fallback.escalation_kind,
            )
        return stage, PricingExplanationExecutionResult(
            response_text=output.response_text,
            outcome=output.outcome,
            validation_outcome=PricingValidationOutcome.ACCEPTED,
            validation_reason="grounded_output_accepted",
            ai_invoked=True,
            escalation_kind=(
                "authoritative_information_unavailable"
                if output.outcome.value in {"escalation_required", "insufficient_verified_pricing"}
                else None
            ),
        )

    async def execute_conversation_expertise(
        self,
        lead: Lead,
        inbound: str,
        skill_context: AgentSkillExecutionContext,
        *,
        communication_channel: str | None = None,
        conversation_history: list[ConversationMessage] | None = None,
        current_stage: SalesStage | None = None,
        qualification_context: SalesQualificationContext | None = None,
    ) -> tuple[SalesStage, ConversationExpertiseExecutionResult]:
        """Execute one authorized 296E Skill through existing HIRI boundaries."""

        if self.context.workspace is None:
            raise RuntimeError("A server-resolved workspace is required for AgentSkill execution")
        if (
            skill_context.workspace_id != self.context.workspace.id
            or skill_context.skill_key
            not in {NEEDS_DISCOVERY_KEY, OBJECTION_HANDLING_KEY, BUYER_INDECISION_KEY}
            or skill_context.skill_version != CONVERSATION_EXPERTISE_VERSION
            or skill_context.effective_tool_ceiling
        ):
            raise PermissionError("Sales conversation AgentSkill context is not authorized")
        definition = sales_agent_skill_registry().resolve(
            skill_context.skill_key,
            skill_context.skill_version,
        )
        if (
            skill_context.input_contract != definition.input_contract
            or skill_context.output_contract != definition.output_contract
            or skill_context.validator != definition.validator
            or skill_context.instruction_component != definition.instruction_component
            or skill_context.attribution_identifier != definition.attribution_identifier
        ):
            raise PermissionError("Sales conversation AgentSkill definition does not match context")
        components = conversation_expertise_components(definition)
        if components.input_contract is not ConversationExpertiseInput:
            raise RuntimeError("Sales conversation AgentSkill input is not registered")
        expected_outputs = {
            NEEDS_DISCOVERY_KEY: NeedsDiscoveryOutput,
            OBJECTION_HANDLING_KEY: ObjectionHandlingOutput,
            BUYER_INDECISION_KEY: BuyerIndecisionOutput,
        }
        if components.output_contract is not expected_outputs[skill_context.skill_key]:
            raise RuntimeError("Sales conversation AgentSkill output is not registered")

        stage = self.detect_stage(inbound)
        canonical_stage = current_stage or stage
        history = (
            conversation_history
            if conversation_history is not None
            else self.context.repository.conversation_history(lead.id)
        )
        products = self.context.repository.list_products(lead.tenant_id)
        source = self._conversation_expertise_input(
            lead=lead,
            inbound=inbound,
            communication_channel=communication_channel,
            stage=canonical_stage,
            products=products,
            history=history,
        )
        deterministic_objection = (
            skill_context.skill_key == OBJECTION_HANDLING_KEY
            and objection_type_for(inbound) in {ObjectionType.GUARANTEE, ObjectionType.INTEGRATION}
        )
        if self.context.settings.llm_mode == "demo" or deterministic_objection:
            return stage, safe_conversation_expertise_result(skill_context.skill_key, source)

        code_switching_instruction = (
            " Preserve the customer's natural French/Tunisian code-switching where practical."
            if source.preserve_code_switching
            else ""
        )
        rendered_prompt = self._compose_prompt(
            lead=lead,
            inbound=inbound,
            stage=canonical_stage,
            products=products,
            conversation_history=history,
            qualification_context=qualification_context,
            skill_instruction=SalesSkillInstruction(
                identifier=skill_context.instruction_component,
                content=skill_instructions(skill_context.skill_key) + code_switching_instruction,
            ),
        ).render()
        if self.context.ai_invocation_gateway is None:
            raise RuntimeError("No AI invocation gateway is configured for AgentSkill")
        invocation = await self.context.ai_invocation_gateway.invoke(
            AIInvocationGatewayRequest(
                workspace=self.context.workspace,
                task=AIModelRoutingTask.CONTEXTUAL_CUSTOMER_RESPONSE,
                task_identifier=skill_context.attribution_identifier,
                agent_identifier="sales_conversation",
                system_prompt=rendered_prompt.system_prompt,
                user_prompt=rendered_prompt.user_prompt,
                conversation_id=lead.id,
                sales_stage=canonical_stage,
                attribution=skill_context.ai_execution_attribution,
            )
        )
        if invocation.content is None:
            raise RuntimeError("Sales conversation AgentSkill requires an LLM completion")
        try:
            output = parse_conversation_expertise_output(
                skill_context.skill_key,
                invocation.content,
            )
            components.validator.validate(output, source)
        except (ConversationExpertiseContractError, ConversationExpertiseValidationError) as exc:
            fallback = safe_conversation_expertise_result(
                skill_context.skill_key,
                source,
                validation_rejected=True,
            )
            return stage, ConversationExpertiseExecutionResult(
                response_text=fallback.response_text,
                outcome=fallback.outcome,
                validation_outcome=fallback.validation_outcome,
                validation_reason=type(exc).__name__,
                ai_invoked=True,
                structured_result=fallback.structured_result,
                escalation_kind=fallback.escalation_kind,
            )
        return stage, accepted_conversation_expertise_result(output)

    def _conversation_expertise_input(
        self,
        *,
        lead: Lead,
        inbound: str,
        communication_channel: str | None,
        stage: SalesStage,
        products: list[Product],
        history: list[ConversationMessage],
    ) -> ConversationExpertiseInput:
        assert self.context.workspace is not None
        workspace = self.context.workspace
        style = select_sales_communication_style(
            customer_message=inbound,
            workspace_preferred_language=workspace.sales_preferred_language,
            workspace_preferred_script=workspace.sales_preferred_script,
            prior_customer_messages=self._prior_customer_messages(history),
        )
        lead_facts = [
            SalesEvidenceFact(
                f"Lead name: {lead.full_name}",
                SalesEvidenceClassification.CONFIRMED,
            ),
            SalesEvidenceFact(
                f"Company: {lead.company_name}",
                SalesEvidenceClassification.CONFIRMED,
            ),
        ]
        if lead.job_title:
            lead_facts.append(
                SalesEvidenceFact(
                    f"Job title: {lead.job_title}",
                    SalesEvidenceClassification.CONFIRMED,
                )
            )
        return ConversationExpertiseInput(
            workspace_id=workspace.id,
            customer_message=inbound,
            communication_channel=communication_channel,
            conversation_context=tuple(
                ConversationExpertiseMessage(message.direction, message.content)
                for message in history
            ),
            sales_stage=stage,
            lead_facts=tuple(lead_facts),
            products=self._product_context(products),
            language=style.language,
            script=style.script,
            preserve_code_switching=preserve_code_switching(inbound),
            workspace_instructions=workspace.sales_instructions,
        )

    def _pricing_input(
        self,
        *,
        inbound: str,
        stage: SalesStage,
        products: list[Product],
        history: list[ConversationMessage],
    ) -> PricingExplanationInput:
        assert self.context.workspace is not None
        workspace = self.context.workspace
        style = select_sales_communication_style(
            customer_message=inbound,
            workspace_preferred_language=workspace.sales_preferred_language,
            workspace_preferred_script=workspace.sales_preferred_script,
            prior_customer_messages=self._prior_customer_messages(history),
        )
        product_facts = canonical_product_facts(products)
        selected, classification, reason = analyze_pricing_evidence(
            inbound,
            product_facts,
        )
        return PricingExplanationInput(
            workspace_id=workspace.id,
            customer_message=inbound,
            conversation_context=tuple(
                PricingConversationMessage(
                    direction=message.direction,
                    content=message.content,
                )
                for message in history
            ),
            sales_stage=stage,
            products=product_facts,
            selected_products=selected,
            evidence_classification=classification,
            evidence_reason=reason,
            language=style.language,
            script=style.script,
            preserve_code_switching=preserve_code_switching(inbound),
            workspace_instructions=workspace.sales_instructions,
        )
