import re

from app.departments.sales.agents.base import AgentContext
from app.departments.sales.language_policy import (
    render_sales_communication_instruction,
    select_sales_communication_style,
    select_sales_tone,
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
    WorkspaceSalesInstructions,
)
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
                workspace_instructions=workspace_instructions,
                business_context=SalesBusinessContext(
                    company_name=self.context.workspace.name if self.context.workspace else None,
                    products=self._product_context(products),
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
