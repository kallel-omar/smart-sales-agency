import re

from uuid import UUID

from app.departments.sales.agents.base import AgentContext
from app.departments.sales.prompt_composition import (
    PromptComposition,
    PromptCompositionInput,
    PromptMessage,
    PromptMessageRole,
    PromptTrustLevel,
    SalesBusinessContext,
    SalesProductContext,
    SalesPromptComposer,
    SALES_COMMERCIAL_GROUNDING_POLICY,
    SALES_DEPARTMENT_POLICY,
    SALES_PLATFORM_POLICY,
    WorkspaceSalesInstructions,
)
from app.models import Lead, Product, SalesStage
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

    def _conversation_context(self, lead_id: UUID) -> tuple[PromptMessage, ...]:
        """Map persisted history to role-aware, untrusted conversation context."""

        messages: list[PromptMessage] = []
        for message in self.context.repository.conversation_history(lead_id):
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

    def _compose_prompt(
        self,
        *,
        lead: Lead,
        inbound: str,
        stage: SalesStage,
        products: list[Product],
    ) -> PromptComposition:
        """Build transient Sales context while keeping customer text untrusted."""

        workspace_instructions = None
        if self.context.workspace and self.context.workspace.sales_instructions:
            workspace_instructions = WorkspaceSalesInstructions(
                content=self.context.workspace.sales_instructions
            )

        return SalesPromptComposer().compose(
            PromptCompositionInput(
                platform_policy=SALES_PLATFORM_POLICY,
                department_policy=SALES_DEPARTMENT_POLICY,
                commercial_grounding_policy=SALES_COMMERCIAL_GROUNDING_POLICY,
                agent_instructions="Ask one useful next question.",
                workspace_instructions=workspace_instructions,
                business_context=SalesBusinessContext(
                    company_name=self.context.workspace.name if self.context.workspace else None,
                    products=self._product_context(products),
                ),
                conversation_messages=self._conversation_context(lead.id),
                current_task=(
                    f"Sales stage: {stage.value}\nLead: {lead.full_name} at {lead.company_name}\n"
                    f"Customer message: {inbound}"
                ),
            )
        )

    async def draft_reply(self, lead: Lead, inbound: str) -> tuple[SalesStage, str]:
        stage = self.detect_stage(inbound)
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
            rendered_prompt = self._compose_prompt(
                lead=lead,
                inbound=inbound,
                stage=stage,
                products=products,
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
                    sales_stage=stage,
                )
            )
            if invocation.content is None:
                raise RuntimeError("Sales conversation requires an LLM completion")
            reply = invocation.content

        return stage, reply
