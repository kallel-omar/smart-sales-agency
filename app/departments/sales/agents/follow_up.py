from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core.agent_skill_execution import AgentSkillExecutionContext
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.evidence import (
    SalesEvidenceClassification,
    SalesEvidenceItem,
    SalesEvidenceSourceType,
)
from app.departments.sales.follow_up_expertise import (
    FOLLOWUP_EXPERTISE_VERSION,
    FOLLOWUP_MESSAGE_GENERATION_KEY,
    FOLLOWUP_MESSAGE_INSTRUCTIONS,
    FOLLOWUP_PLANNER_KEY,
    FollowUpContractError,
    FollowUpConversationMessage,
    FollowUpMessageInput,
    FollowUpMessageOutcome,
    FollowUpMessageOutput,
    FollowUpPlannerInput,
    FollowUpPlanOutput,
    FollowUpSkillExecutionResult,
    FollowUpValidationError,
    PriorFollowUp,
    configured_follow_up_message,
    followup_message_components,
    followup_planner_components,
    message_execution_result,
    plan_follow_up,
    planner_execution_result,
    safe_follow_up_message,
)
from app.departments.sales.language_policy import (
    render_sales_communication_instruction,
    select_sales_communication_style,
    select_sales_tone,
)
from app.departments.sales.pricing_explanation import preserve_code_switching
from app.departments.sales.prompt_composition import (
    SALES_COMMERCIAL_GROUNDING_POLICY,
    SALES_DEPARTMENT_POLICY,
    SALES_PLATFORM_POLICY,
    PromptCompositionInput,
    SalesLanguageToneInstruction,
    SalesPromptComposer,
    SalesSkillInstruction,
    UntrustedPromptContext,
    WorkspaceSalesInstructions,
)
from app.departments.sales.skills import sales_agent_skill_registry
from app.models import ConversationMessage, FollowUpTask, Lead, LeadStatus
from app.services.ai_invocation_gateway import AIInvocationGateway, AIInvocationGatewayRequest
from app.services.ai_model_routing import AIModelRoutingTask


class FollowUpAgent:
    def __init__(self, session: Session, context: AgentContext | None = None):
        self.session = session
        self.context = context

    def schedule(
        self,
        lead: Lead,
        reason: str,
        delay_days: int = 2,
    ) -> FollowUpTask:
        task = FollowUpTask(
            lead_id=lead.id,
            due_at=datetime.now(UTC) + timedelta(days=max(1, delay_days)),
            reason=reason,
        )

        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)

        return task

    def decide(
        self,
        task: FollowUpTask,
        lead: Lead,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Compatibility decision boundary without governed skill execution."""

        if LeadStatus(lead.status) in {
            LeadStatus.WON,
            LeadStatus.LOST,
            LeadStatus.UNQUALIFIED,
        }:
            return {
                "action": "no_send",
                "reason": f"lead_status_{LeadStatus(lead.status).value}",
            }

        outbound_fields = (
            "message",
            "integration_account_id",
            "channel",
            "recipient",
        )
        if all(context.get(field) for field in outbound_fields):
            return {
                "action": "send",
                "reason": task.reason,
                **{field: context[field] for field in outbound_fields},
            }
        raise ValueError("Follow-up outbound context is not configured")

    async def execute_governed(
        self,
        task: FollowUpTask,
        lead: Lead,
        work_item_input: dict[str, Any],
        contexts: tuple[AgentSkillExecutionContext, ...],
    ) -> dict[str, Any]:
        if self.context is None or self.context.workspace is None:
            raise RuntimeError("Governed follow-up requires server-resolved context")
        planner_context, message_context = self._validated_contexts(contexts)
        planner_input = self._planner_input(task, lead)
        planner_definition = sales_agent_skill_registry().resolve(
            FOLLOWUP_PLANNER_KEY,
            FOLLOWUP_EXPERTISE_VERSION,
        )
        planner_components = followup_planner_components(planner_definition)
        if not isinstance(planner_input, planner_components.input_contract):
            raise TypeError("Follow-up planner input contract is invalid")
        plan = plan_follow_up(planner_input)
        plan = planner_components.validator.validate(plan, planner_input)
        if not isinstance(plan, planner_components.output_contract):
            raise TypeError("Follow-up planner output contract is invalid")
        planner_result = planner_execution_result(plan)
        skill_results = [self._skill_metadata(planner_context, planner_result)]
        if not plan.should_follow_up:
            return {
                "action": "no_send",
                "reason": plan.reason,
                "agent_skills": skill_results,
            }

        outbound = self._outbound_context(work_item_input)
        message_input = self._message_input(
            task,
            lead,
            plan,
            work_item_input.get("message"),
        )
        message_result, message = await self._generate_message(
            lead,
            message_input,
            message_context,
        )
        skill_results.append(self._skill_metadata(message_context, message_result))
        if message.outcome is FollowUpMessageOutcome.ESCALATION_REQUIRED:
            return {
                "action": "no_send",
                "reason": message.escalation_reason or "follow_up_message_escalation",
                "agent_skills": skill_results,
            }
        return {
            "action": "send",
            "reason": task.reason,
            "message": message.response_text,
            **outbound,
            "agent_skills": skill_results,
        }

    def _planner_input(self, task: FollowUpTask, lead: Lead) -> FollowUpPlannerInput:
        assert self.context is not None and self.context.workspace is not None
        history = self.context.repository.conversation_history(lead.id)
        prior = self.session.exec(
            select(FollowUpTask).where(
                FollowUpTask.lead_id == lead.id,
                FollowUpTask.id != task.id,
            )
        ).all()
        return FollowUpPlannerInput(
            workspace_id=self.context.workspace.id,
            lead_id=lead.id,
            task_id=task.id,
            lead_status=LeadStatus(lead.status),
            reason=task.reason,
            due_at=_aware(task.due_at),
            task_created_at=_aware(task.created_at),
            conversation=tuple(
                FollowUpConversationMessage(
                    reference=f"conversation.{message.id}",
                    direction=message.direction,
                    content=message.content[:1_000],
                    created_at=_aware(message.created_at),
                )
                for message in history
            ),
            prior_follow_ups=tuple(
                PriorFollowUp(
                    task_id=item.id,
                    reason=item.reason,
                    status=item.status,
                    created_at=_aware(item.created_at),
                )
                for item in prior
            ),
            active_handoff=(
                self.context.repository.get_sales_handoff(
                    self.context.workspace,
                    lead.id,
                )
                is not None
            ),
            workspace_instructions=self.context.workspace.sales_instructions,
        )

    def _message_input(
        self,
        task: FollowUpTask,
        lead: Lead,
        plan: FollowUpPlanOutput,
        configured_message: object,
    ) -> FollowUpMessageInput:
        assert self.context is not None and self.context.workspace is not None
        history = self.context.repository.conversation_history(lead.id)
        inbound = tuple(
            message.content for message in history if message.direction == "inbound"
        )
        latest_customer_message = inbound[-1] if inbound else task.reason
        workspace = self.context.workspace
        style = select_sales_communication_style(
            customer_message=latest_customer_message,
            workspace_preferred_language=workspace.sales_preferred_language,
            workspace_preferred_script=workspace.sales_preferred_script,
            prior_customer_messages=inbound,
        )
        evidence = [
            SalesEvidenceItem(
                SalesEvidenceClassification.CONFIRMED,
                task.reason,
                SalesEvidenceSourceType.FOLLOW_UP_TASK,
                f"follow_up_task.{task.id}.reason",
                _aware(task.created_at).isoformat(),
            ),
            SalesEvidenceItem(
                SalesEvidenceClassification.CONFIRMED,
                LeadStatus(lead.status).value,
                SalesEvidenceSourceType.LEAD_RECORD,
                "lead.status",
                _aware(lead.updated_at).isoformat(),
            ),
        ]
        evidence.extend(self._conversation_evidence(history))
        return FollowUpMessageInput(
            workspace_id=workspace.id,
            lead_id=lead.id,
            plan=plan,
            style=style,
            lead_display_name=lead.full_name,
            evidence=tuple(evidence),
            previous_outbound_messages=tuple(
                message.content for message in history if message.direction == "outbound"
            ),
            configured_message=(
                configured_message.strip()
                if isinstance(configured_message, str) and configured_message.strip()
                else None
            ),
            preserve_code_switching=preserve_code_switching(latest_customer_message),
        )

    async def _generate_message(
        self,
        lead: Lead,
        source: FollowUpMessageInput,
        context: AgentSkillExecutionContext,
    ) -> tuple[FollowUpSkillExecutionResult, FollowUpMessageOutput]:
        assert self.context is not None and self.context.workspace is not None
        definition = sales_agent_skill_registry().resolve(
            FOLLOWUP_MESSAGE_GENERATION_KEY,
            FOLLOWUP_EXPERTISE_VERSION,
        )
        components = followup_message_components(definition)
        if not isinstance(source, components.input_contract):
            raise TypeError("Follow-up message input contract is invalid")
        if source.configured_message:
            try:
                output = configured_follow_up_message(source)
                output = components.validator.validate(output, source)
            except FollowUpValidationError as exc:
                fallback = safe_follow_up_message(source)
                return (
                    message_execution_result(
                        fallback,
                        ai_invoked=False,
                        rejected=True,
                        validation_reason=type(exc).__name__,
                    ),
                    fallback,
                )
            return message_execution_result(output, ai_invoked=False), output
        if self.context.settings.llm_mode == "demo":
            output = safe_follow_up_message(source)
            if output.outcome is FollowUpMessageOutcome.DRAFT_READY:
                output = components.validator.validate(output, source)
            return message_execution_result(output, ai_invoked=False), output

        gateway = self.context.ai_invocation_gateway or AIInvocationGateway(
            self.session,
            self.context.settings,
        )
        rendered = self._message_prompt(source).render()
        invocation = await gateway.invoke(
            AIInvocationGatewayRequest(
                workspace=self.context.workspace,
                task=AIModelRoutingTask.CONTEXTUAL_CUSTOMER_RESPONSE,
                task_identifier=context.attribution_identifier,
                agent_identifier="sales_follow_up",
                system_prompt=rendered.system_prompt,
                user_prompt=rendered.user_prompt,
                conversation_id=lead.id,
                attribution=context.ai_execution_attribution,
            )
        )
        try:
            if invocation.content is None:
                raise FollowUpContractError("Follow-up output is missing")
            output = FollowUpMessageOutput.from_json(invocation.content)
            output = components.validator.validate(output, source)
        except (FollowUpContractError, FollowUpValidationError) as exc:
            fallback = safe_follow_up_message(source)
            return (
                message_execution_result(
                    fallback,
                    ai_invoked=True,
                    rejected=True,
                    validation_reason=type(exc).__name__,
                ),
                fallback,
            )
        return message_execution_result(output, ai_invoked=True), output

    def _message_prompt(self, source: FollowUpMessageInput):
        assert self.context is not None and self.context.workspace is not None
        workspace = self.context.workspace
        workspace_instructions = (
            WorkspaceSalesInstructions(content=workspace.sales_instructions)
            if workspace.sales_instructions
            else None
        )
        tone = select_sales_tone(workspace.sales_preferred_tone)
        code_switch = (
            " Preserve the customer’s natural language switching where practical."
            if source.preserve_code_switching
            else ""
        )
        evidence = "\n".join(
            f"{item.source_reference}: [{item.classification.value}] {item.claim}"
            for item in source.evidence
        )
        return SalesPromptComposer().compose(
            PromptCompositionInput(
                platform_policy=SALES_PLATFORM_POLICY,
                department_policy=SALES_DEPARTMENT_POLICY,
                commercial_grounding_policy=SALES_COMMERCIAL_GROUNDING_POLICY,
                agent_instructions=(
                    "You are the governed HIRI Sales Follow-Up employee. Draft one "
                    "concise continuation and do not perform delivery."
                ),
                language_tone_instruction=SalesLanguageToneInstruction(
                    content=render_sales_communication_instruction(
                        language=source.style.language,
                        script=source.style.script,
                        tone=tone,
                    )
                ),
                skill_instruction=SalesSkillInstruction(
                    identifier="sales.followup_message_generation.instruction.v1",
                    content=FOLLOWUP_MESSAGE_INSTRUCTIONS + code_switch,
                ),
                workspace_instructions=workspace_instructions,
                untrusted_context=(
                    UntrustedPromptContext(
                        label="Bounded follow-up evidence",
                        content=evidence,
                    ),
                ),
                current_task=(
                    f"Objective: {source.plan.objective}\n"
                    f"Allowed evidence references: {sorted(source.evidence_references())}"
                ),
            )
        )

    @staticmethod
    def _conversation_evidence(
        history: list[ConversationMessage],
    ) -> list[SalesEvidenceItem]:
        return [
            SalesEvidenceItem(
                SalesEvidenceClassification.CONFIRMED,
                message.content[:500],
                SalesEvidenceSourceType.CONVERSATION,
                f"conversation.{message.id}",
                _aware(message.created_at).isoformat(),
            )
            for message in history
            if message.content.strip()
        ]

    @staticmethod
    def _outbound_context(source: dict[str, Any]) -> dict[str, Any]:
        required = ("integration_account_id", "channel", "recipient")
        if not all(source.get(field) for field in required):
            raise ValueError("Follow-up outbound context is not configured")
        return {field: source[field] for field in required}

    @staticmethod
    def _validated_contexts(
        contexts: tuple[AgentSkillExecutionContext, ...],
    ) -> tuple[AgentSkillExecutionContext, AgentSkillExecutionContext]:
        if len(contexts) != 2:
            raise RuntimeError("Follow-up WorkItem requires exactly two governed skills")
        planner, message = contexts
        actual = (
            (planner.skill_key, planner.skill_version),
            (message.skill_key, message.skill_version),
        )
        expected = (
            (FOLLOWUP_PLANNER_KEY, FOLLOWUP_EXPERTISE_VERSION),
            (FOLLOWUP_MESSAGE_GENERATION_KEY, FOLLOWUP_EXPERTISE_VERSION),
        )
        if actual != expected or planner.effective_tool_ceiling or message.effective_tool_ceiling:
            raise RuntimeError("Follow-up AgentSkill execution context is invalid")
        return planner, message

    @staticmethod
    def _skill_metadata(
        context: AgentSkillExecutionContext,
        result: FollowUpSkillExecutionResult,
    ) -> dict[str, object]:
        return {
            "key": context.skill_key,
            "version": context.skill_version,
            "outcome": result.outcome.value,
            "validation_outcome": result.validation_outcome.value,
            "result": result.structured_result,
        }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
