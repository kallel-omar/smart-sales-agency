"""Application boundary for one workspace-scoped Sales conversation turn."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session

from app.config import Settings
from app.core.agent_skill_execution import AgentSkillExecutionContext
from app.core.ai_execution_attribution import AIExecutionAttribution
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.agents.sales_agent import SalesConversationAgent
from app.departments.sales.conversation_expertise import (
    ConversationExpertiseExecutionResult,
)
from app.departments.sales.handoff_policy import (
    SalesCommercialEscalationType,
    SalesHandoffDecision,
    SalesHandoffPolicy,
    SalesHandoffSignals,
    derive_customer_handoff_signals,
    merge_sales_handoff_signals,
    render_sales_handoff_reply,
)
from app.departments.sales.pricing_explanation import PricingExplanationExecutionResult
from app.departments.sales.services.stage_transition_service import (
    SalesStageTransitionService,
)
from app.models import (
    ConversationMessage,
    SalesHandoffReasonCode,
    SalesStage,
    Workspace,
)
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.qualification_collection import QualificationCollectionService
from app.services.repository import NotFoundError, SalesRepository


@dataclass(frozen=True, slots=True)
class SalesConversationTurnInput:
    """Trusted application input for one customer turn.

    The workspace is an injected service dependency resolved by the server; it
    is intentionally not accepted from this input or customer request body.
    """

    lead_id: UUID
    channel: str
    customer_message: str
    handoff_signals: SalesHandoffSignals | None = None


@dataclass(frozen=True, slots=True)
class SalesConversationTurnResult:
    """Safe, provider-neutral outcome for a persisted Sales conversation turn."""

    lead_id: UUID
    detected_stage: SalesStage
    draft_reply: str
    approval_id: UUID | None
    handoff_required: bool = False
    handoff_reason_code: SalesHandoffReasonCode | None = None
    ai_invoked: bool = False
    agent_skill: SalesAgentSkillTurnAttribution | None = None


@dataclass(frozen=True, slots=True)
class SalesAgentSkillTurnAttribution:
    """Safe WorkItem-result metadata for one governed AgentSkill execution."""

    key: str
    version: str
    outcome: str
    validation_outcome: str
    structured_result: dict[str, object] | None = None


class SalesConversationTurnService:
    """Coordinate existing domain services for exactly one Sales customer turn.

    It owns turn-level history loading and message persistence. The Sales agent
    owns prompt composition and the central gateway remains the only AI boundary.
    """

    def __init__(
        self,
        *,
        repository: SalesRepository,
        settings: Settings,
        workspace: Workspace,
        ai_invocation_gateway: AIInvocationGateway | None = None,
        ai_execution_attribution: AIExecutionAttribution | None = None,
        agent_skill_execution_context: AgentSkillExecutionContext | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.workspace = workspace
        self.ai_invocation_gateway = ai_invocation_gateway or AIInvocationGateway(
            repository.session,
            settings,
        )
        self.ai_execution_attribution = ai_execution_attribution
        self.agent_skill_execution_context = agent_skill_execution_context

    async def process(
        self,
        source: SalesConversationTurnInput,
    ) -> SalesConversationTurnResult:
        """Resolve, prepare, execute, and persist one Sales turn once."""

        lead = self.repository.get_lead(source.lead_id)
        if lead.tenant_id != self.workspace.slug:
            raise NotFoundError("Lead not found")

        # The repository's existing bounded, chronological history contract is
        # loaded once and supplied to the agent for all prompt-context uses.
        history = self.repository.conversation_history(lead.id)
        canonical_stage = SalesStageTransitionService(
            repository=self.repository,
            workspace=self.workspace,
        ).canonical_stage_for(lead)
        handoff = self._handoff_decision(
            lead.id,
            source.handoff_signals,
            source.customer_message,
        )
        agent = SalesConversationAgent(self._agent_context())
        collection_service = (
            QualificationCollectionService(
                self.repository.session,
                self.settings,
                ai_invocation_gateway=self.ai_invocation_gateway,
            )
            if isinstance(self.repository.session, Session)
            else None
        )
        qualification_context = (
            None
            if handoff.human_attention_required or collection_service is None
            else collection_service.pending_context(
                self.workspace,
                lead,
                source.customer_message,
            )
        )

        if handoff.human_attention_required:
            assert handoff.reason_code is not None and handoff.explanation is not None
            self.repository.ensure_sales_handoff(
                workspace=self.workspace,
                lead=lead,
                reason_code=handoff.reason_code,
                explanation=handoff.explanation,
            )
            stage = agent.detect_stage(source.customer_message)
            reply = render_sales_handoff_reply(handoff)
            ai_invoked = False
            agent_skill = None
        elif self.agent_skill_execution_context is not None:
            if self.agent_skill_execution_context.skill_key == "pricing_explanation":
                stage, skill_result = await agent.execute_pricing_explanation(
                    lead,
                    source.customer_message,
                    self.agent_skill_execution_context,
                    conversation_history=history,
                    current_stage=canonical_stage,
                    qualification_context=qualification_context,
                )
                structured_result = None
            else:
                stage, skill_result = await agent.execute_conversation_expertise(
                    lead,
                    source.customer_message,
                    self.agent_skill_execution_context,
                    communication_channel=source.channel,
                    conversation_history=history,
                    current_stage=canonical_stage,
                    qualification_context=qualification_context,
                )
                structured_result = skill_result.structured_result
            reply = skill_result.response_text
            ai_invoked = skill_result.ai_invoked
            agent_skill = SalesAgentSkillTurnAttribution(
                key=self.agent_skill_execution_context.skill_key,
                version=self.agent_skill_execution_context.skill_version,
                outcome=skill_result.outcome.value,
                validation_outcome=skill_result.validation_outcome.value,
                structured_result=structured_result,
            )
            skill_handoff = self._skill_handoff_decision(skill_result)
            if skill_handoff.human_attention_required:
                assert (
                    skill_handoff.reason_code is not None and skill_handoff.explanation is not None
                )
                self.repository.ensure_sales_handoff(
                    workspace=self.workspace,
                    lead=lead,
                    reason_code=skill_handoff.reason_code,
                    explanation=skill_handoff.explanation,
                )
                handoff = skill_handoff
        else:
            stage, reply = await agent.draft_reply(
                lead,
                source.customer_message,
                conversation_history=history,
                current_stage=canonical_stage,
                qualification_context=qualification_context,
            )
            ai_invoked = self.settings.llm_mode != "demo"
            agent_skill = None

        # The turn service is the only owner of reply-message persistence.
        # Existing approval behavior deliberately remains unchanged.
        inbound_message = self.repository.add_message(
            ConversationMessage(
                lead_id=lead.id,
                direction="inbound",
                channel=source.channel,
                stage=stage,
                content=source.customer_message,
            )
        )

        if not handoff.human_attention_required and collection_service is not None:
            await collection_service.process_persisted_message(
                self.workspace,
                lead,
                inbound_message,
            )

        approval_id: UUID | None = None
        if self.settings.require_human_approval:
            approval = self.repository.create_approval(
                lead_id=lead.id,
                channel=source.channel,
                payload={
                    "recipient": lead.email or lead.phone or lead.full_name,
                    "content": reply,
                    "stage": stage.value,
                },
            )
            approval_id = approval.id
        else:
            self.repository.add_message(
                ConversationMessage(
                    lead_id=lead.id,
                    direction="outbound",
                    channel=source.channel,
                    stage=stage,
                    content=reply,
                )
            )

        return SalesConversationTurnResult(
            lead_id=lead.id,
            detected_stage=stage,
            draft_reply=reply,
            approval_id=approval_id,
            handoff_required=handoff.human_attention_required,
            handoff_reason_code=handoff.reason_code,
            ai_invoked=ai_invoked,
            agent_skill=agent_skill,
        )

    def _agent_context(self) -> AgentContext:
        return AgentContext(
            settings=self.settings,
            repository=self.repository,
            llm=None,
            workspace=self.workspace,
            ai_invocation_gateway=self.ai_invocation_gateway,
            ai_execution_attribution=self.ai_execution_attribution,
        )

    @staticmethod
    def _skill_handoff_decision(
        result: PricingExplanationExecutionResult | ConversationExpertiseExecutionResult,
    ) -> SalesHandoffDecision:
        if result.escalation_kind == "unsupported_discount":
            signals = SalesHandoffSignals(
                commercial_escalation=SalesCommercialEscalationType.UNSUPPORTED_DISCOUNT
            )
        elif result.escalation_kind == "custom_pricing":
            signals = SalesHandoffSignals(
                commercial_escalation=SalesCommercialEscalationType.CUSTOM_PRICING
            )
        elif result.escalation_kind == "unsupported_commitment":
            signals = SalesHandoffSignals(
                commercial_escalation=SalesCommercialEscalationType.UNSUPPORTED_COMMITMENT
            )
        elif result.escalation_kind is not None:
            signals = SalesHandoffSignals(authoritative_information_unavailable=True)
        else:
            signals = SalesHandoffSignals()
        return SalesHandoffPolicy().decide(signals)

    def _handoff_decision(
        self,
        lead_id: UUID,
        signals: SalesHandoffSignals | None,
        customer_message: str,
    ) -> SalesHandoffDecision:
        existing = self.repository.get_sales_handoff(self.workspace, lead_id)
        if existing is not None:
            return SalesHandoffDecision(
                human_attention_required=True,
                reason_code=existing.reason_code,
                explanation=existing.explanation,
            )
        return SalesHandoffPolicy().decide(
            merge_sales_handoff_signals(
                signals,
                derive_customer_handoff_signals(customer_message),
            )
        )
