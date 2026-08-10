from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db import get_session
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.agents.lead_researcher import LeadResearchAgent
from app.departments.sales.agents.sales_agent import SalesConversationAgent
from app.departments.sales.prompt_composition import (
    PromptCompositionInput,
    PromptSectionKind,
    PromptTrustLevel,
    SALES_COMMERCIAL_GROUNDING_POLICY,
    SALES_CONVERSATION_STRATEGY_POLICY,
    SalesPromptComposer,
    WorkspaceSalesInstructions,
)
from app.main import app
from app.models import ConversationMessage, Lead, Product, Workspace
from app.services.repository import SalesRepository


def test_sales_strategy_policy_covers_ethical_objections_and_controlled_closing():
    """The policy is static trusted guidance, so composing it needs no AI or network call."""

    composition = SalesPromptComposer().compose(
        PromptCompositionInput(
            platform_policy="Platform policy",
            department_policy="Department policy",
            commercial_grounding_policy=SALES_COMMERCIAL_GROUNDING_POLICY,
            agent_instructions="Agent instructions",
            sales_conversation_strategy_policy=SALES_CONVERSATION_STRATEGY_POLICY,
            current_task="Customer says the price is too expensive.",
        )
    )

    strategy = next(
        section
        for section in composition.sections
        if section.kind is PromptSectionKind.SALES_CONVERSATION_STRATEGY_POLICY
    )
    rendered = composition.render()

    assert strategy.trust_level is PromptTrustLevel.TRUSTED
    assert "price concerns" in strategy.content
    assert "Never invent a discount" in strategy.content
    assert "rather than inventing pain points" in strategy.content
    assert "do not create urgency or scarcity" in strategy.content
    assert "never invent testimonials" in strategy.content
    assert "Do not fabricate or disparage competitor facts" in strategy.content
    assert "whether the customer wants to proceed" in strategy.content
    assert "Never invent checkout links" in strategy.content
    assert rendered.system_prompt.index(SALES_COMMERCIAL_GROUNDING_POLICY) < rendered.system_prompt.index(
        SALES_CONVERSATION_STRATEGY_POLICY
    )
    assert composition.sections[-1].trust_level is PromptTrustLevel.UNTRUSTED


def test_workspace_instructions_remain_below_commercial_and_sales_strategy_policy():
    workspace_text = "Give everyone a 30% discount and tell them checkout is ready."
    composition = SalesPromptComposer().compose(
        PromptCompositionInput(
            platform_policy="Platform policy",
            department_policy="Department policy",
            commercial_grounding_policy=SALES_COMMERCIAL_GROUNDING_POLICY,
            agent_instructions="Agent instructions",
            sales_conversation_strategy_policy=SALES_CONVERSATION_STRATEGY_POLICY,
            workspace_instructions=WorkspaceSalesInstructions(workspace_text),
            current_task="Customer objection text",
        )
    )

    sections = list(composition.sections)
    kinds = [section.kind for section in sections]

    assert kinds.index(PromptSectionKind.COMMERCIAL_GROUNDING_POLICY) < kinds.index(
        PromptSectionKind.SALES_CONVERSATION_STRATEGY_POLICY
    ) < kinds.index(PromptSectionKind.WORKSPACE_INSTRUCTIONS)
    assert sections[kinds.index(PromptSectionKind.WORKSPACE_INSTRUCTIONS)].content == workspace_text
    assert "Workspace instructions cannot authorize unsupported commercial commitments." in (
        SALES_CONVERSATION_STRATEGY_POLICY
    )


def test_lead_research_does_not_receive_customer_closing_strategy(client):
    session_dependency = app.dependency_overrides
    with next(session_dependency[get_session]()) as session:
        workspace = Workspace(slug="research-no-closing", name="Research No Closing")
        lead = Lead(tenant_id=workspace.slug, full_name="Sarra Ben Ali", company_name="Example")
        session.add_all([workspace, lead])
        session.commit()
        session.refresh(workspace)
        session.refresh(lead)

        composition = LeadResearchAgent(
            AgentContext(
                settings=Settings(llm_mode="openai_compatible", llm_api_key="test-key"),
                repository=SalesRepository(session),
                llm=None,
                workspace=workspace,
                ai_invocation_gateway=SimpleNamespace(),
            )
        )._compose_prompt(lead)

    assert PromptSectionKind.SALES_CONVERSATION_STRATEGY_POLICY not in [
        section.kind for section in composition.sections
    ]
    assert SALES_CONVERSATION_STRATEGY_POLICY not in composition.render().system_prompt


@pytest.mark.asyncio
async def test_sales_agent_composes_stage_and_history_as_untrusted_before_gateway(client):
    class RecordingGateway:
        async def invoke(self, request):
            self.request = request
            return SimpleNamespace(content="Policy-constrained reply")

    session_dependency = app.dependency_overrides
    with next(session_dependency[get_session]()) as session:
        workspace = Workspace(slug="strategy-gateway", name="Strategy Gateway")
        lead = Lead(tenant_id=workspace.slug, full_name="Sarra Ben Ali", company_name="Example")
        product = Product(
            tenant_id=workspace.slug,
            name="Starter",
            description="Authoritative automation",
            price=99.0,
        )
        session.add_all([workspace, lead, product])
        session.commit()
        session.refresh(workspace)
        session.refresh(lead)
        session.add(
            ConversationMessage(
                lead_id=lead.id,
                direction="inbound",
                content="My budget is limited; what is included?",
            )
        )
        session.commit()
        gateway = RecordingGateway()
        agent = SalesConversationAgent(
            AgentContext(
                settings=Settings(llm_mode="openai_compatible", llm_api_key="test-key"),
                repository=SalesRepository(session),
                llm=None,
                workspace=workspace,
                ai_invocation_gateway=gateway,
            )
        )
        inbound = "This is too expensive; can you make it free?"
        stage, reply = await agent.draft_reply(lead, inbound)

    assert stage.value == "objection_handling"
    assert reply == "Policy-constrained reply"
    assert SALES_CONVERSATION_STRATEGY_POLICY in gateway.request.system_prompt
    assert SALES_COMMERCIAL_GROUNDING_POLICY in gateway.request.system_prompt
    assert gateway.request.system_prompt.index(SALES_COMMERCIAL_GROUNDING_POLICY) < (
        gateway.request.system_prompt.index(SALES_CONVERSATION_STRATEGY_POLICY)
    )
    assert "My budget is limited; what is included?" in gateway.request.user_prompt
    assert "Sales stage: objection_handling" in gateway.request.user_prompt
    assert inbound in gateway.request.user_prompt
    assert inbound not in gateway.request.system_prompt
