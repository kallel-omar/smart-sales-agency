import re

from app.departments.sales.agents.base import AgentContext
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

    def _product_context(self, products: list[Product]) -> str:
        if not products:
            return "No product catalog is configured."
        lines = []
        for product in products[:10]:
            price = f"{product.price:.2f}" if product.price is not None else "contact us"
            lines.append(f"- {product.name}: {product.description} | Price: {price}")
        return "\n".join(lines)

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
            system = (
                "You are a helpful B2B sales agent. Be concise, truthful, and non-pushy. "
                "Never invent prices, discounts, stock, guarantees, or customer facts. "
                "Ask one useful next question. A human must approve commitments and outbound messages."
            )
            user = (
                f"Sales stage: {stage.value}\nLead: {lead.full_name} at {lead.company_name}\n"
                f"Customer message: {inbound}\nProduct catalog:\n{self._product_context(products)}"
            )
            if self.context.ai_invocation_gateway is not None:
                if self.context.workspace is None:
                    raise RuntimeError("A server-resolved workspace is required for AI invocation")
                invocation = await self.context.ai_invocation_gateway.invoke(
                    AIInvocationGatewayRequest(
                        workspace=self.context.workspace,
                        task=AIModelRoutingTask.SALES_CONVERSATION,
                        task_identifier="sales.conversation.reply",
                        agent_identifier="sales_conversation",
                        system_prompt=system,
                        user_prompt=user,
                        conversation_id=lead.id,
                        sales_stage=stage,
                    )
                )
                if invocation.content is None:
                    raise RuntimeError("Sales conversation requires an LLM completion")
                reply = invocation.content
            elif self.context.llm is not None:
                # Legacy flows remain until their direct call sites migrate in
                # Tasks 262–265. The representative API reply path supplies
                # the gateway and cannot take this branch.
                reply = await self.context.llm.complete(system, user)
            else:
                raise RuntimeError("No LLM invocation dependency is configured")

        return stage, reply
