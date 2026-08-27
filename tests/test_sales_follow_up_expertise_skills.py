from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.departments.sales.evidence import (
    SalesEvidenceClassification,
    SalesEvidenceItem,
    SalesEvidenceSourceType,
)
from app.departments.sales.follow_up_expertise import (
    FollowUpConversationMessage,
    FollowUpMessageInput,
    FollowUpMessageOutcome,
    FollowUpMessageOutput,
    FollowUpMessageOutputValidator,
    FollowUpPlannerInput,
    FollowUpPlannerOutputValidator,
    FollowUpPlanOutcome,
    FollowUpStopReason,
    FollowUpValidationError,
    PriorFollowUp,
    plan_follow_up,
    safe_follow_up_message,
)
from app.departments.sales.language_policy import (
    SalesCommunicationStyle,
    select_sales_communication_style,
)
from app.departments.sales.pricing_explanation import preserve_code_switching
from app.departments.sales.skills import sales_agent_skill_registry
from app.models import LeadStatus, SalesLanguage, SalesWritingScript

NOW = datetime(2026, 8, 27, 10, tzinfo=UTC)


def message(
    content: str,
    *,
    direction: str = "inbound",
    created_at: datetime = NOW - timedelta(days=3),
    suffix: str = "1",
) -> FollowUpConversationMessage:
    return FollowUpConversationMessage(
        reference=f"conversation.{suffix}",
        direction=direction,
        content=content,
        created_at=created_at,
    )


def planner_input(
    *,
    status: LeadStatus = LeadStatus.NEW,
    reason: str = "Proposal follow-up",
    conversation: tuple[FollowUpConversationMessage, ...] = (),
    prior: tuple[PriorFollowUp, ...] = (),
    active_handoff: bool = False,
    workspace_instructions: str | None = None,
) -> FollowUpPlannerInput:
    return FollowUpPlannerInput(
        workspace_id=uuid4(),
        lead_id=uuid4(),
        task_id=uuid4(),
        lead_status=status,
        reason=reason,
        due_at=NOW,
        task_created_at=NOW - timedelta(days=2),
        conversation=conversation,
        prior_follow_ups=prior,
        active_handoff=active_handoff,
        workspace_instructions=workspace_instructions,
    )


def test_interested_customer_silence_produces_bounded_follow_up_plan() -> None:
    source = planner_input(
        conversation=(
            message("I am interested in the proposal"),
            message(
                "Happy to answer any questions.",
                direction="outbound",
                created_at=NOW - timedelta(days=2, hours=12),
                suffix="2",
            ),
        )
    )

    output = plan_follow_up(source)

    assert output.should_follow_up
    assert output.outcome is FollowUpPlanOutcome.FOLLOW_UP_RECOMMENDED
    assert output.recommended_timing == "existing_scheduled_due_time"
    assert output.not_before == NOW.isoformat()


def test_generic_cold_lead_does_not_receive_aggressive_follow_up() -> None:
    output = plan_follow_up(planner_input(reason="Follow up"))

    assert not output.should_follow_up
    assert output.stop_reason is FollowUpStopReason.INSUFFICIENT_ENGAGEMENT


@pytest.mark.parametrize(
    "opt_out",
    [
        "Don't message me again",
        "Do not contact me",
        "Ne me contactez plus",
        "لا تراسلوني",
    ],
)
def test_explicit_customer_opt_out_stops_follow_up(opt_out: str) -> None:
    output = plan_follow_up(planner_input(conversation=(message(opt_out),)))

    assert not output.should_follow_up
    assert output.stop_reason is FollowUpStopReason.CUSTOMER_OPTED_OUT


@pytest.mark.parametrize(
    ("status", "stop_reason"),
    [
        (LeadStatus.WON, FollowUpStopReason.LEAD_STATUS_WON),
        (LeadStatus.LOST, FollowUpStopReason.LEAD_STATUS_LOST),
        (LeadStatus.UNQUALIFIED, FollowUpStopReason.LEAD_STATUS_UNQUALIFIED),
    ],
)
def test_terminal_or_unqualified_lead_stops_follow_up(
    status: LeadStatus,
    stop_reason: FollowUpStopReason,
) -> None:
    output = plan_follow_up(planner_input(status=status))

    assert not output.should_follow_up
    assert output.stop_reason is stop_reason


def test_active_human_handoff_pauses_follow_up() -> None:
    output = plan_follow_up(planner_input(active_handoff=True))

    assert not output.should_follow_up
    assert output.outcome is FollowUpPlanOutcome.HUMAN_PAUSE
    assert output.stop_reason is FollowUpStopReason.ACTIVE_HUMAN_HANDOFF


def test_qualified_lead_awaiting_reply_allows_bounded_follow_up() -> None:
    output = plan_follow_up(planner_input(status=LeadStatus.QUALIFIED, reason="Follow up"))

    assert output.should_follow_up
    assert output.objective == "Continue the existing conversation about Follow up"


def test_existing_pending_follow_up_prevents_duplicate() -> None:
    existing = PriorFollowUp(uuid4(), "Another follow-up", "pending", NOW - timedelta(days=1))

    output = plan_follow_up(planner_input(prior=(existing,)))

    assert output.stop_reason is FollowUpStopReason.DUPLICATE_PENDING_FOLLOW_UP


def test_newer_customer_reply_invalidates_stale_follow_up() -> None:
    output = plan_follow_up(
        planner_input(
            conversation=(
                message("I have a new question", created_at=NOW - timedelta(days=1)),
            )
        )
    )

    assert output.stop_reason is FollowUpStopReason.NEWER_CUSTOMER_REPLY


def test_repeated_objective_and_excessive_attempts_stop_safely() -> None:
    duplicate = PriorFollowUp(
        uuid4(),
        "Proposal follow-up",
        "completed",
        NOW - timedelta(days=1),
    )
    output = plan_follow_up(planner_input(prior=(duplicate,)))
    assert output.stop_reason is FollowUpStopReason.OBJECTIVE_ALREADY_ATTEMPTED

    attempts = (
        PriorFollowUp(uuid4(), "First attempt", "completed", NOW - timedelta(hours=8)),
        PriorFollowUp(uuid4(), "Second attempt", "completed", NOW - timedelta(hours=4)),
    )
    output = plan_follow_up(planner_input(prior=attempts))
    assert output.stop_reason is FollowUpStopReason.EXCESSIVE_UNANSWERED_FOLLOW_UP


def test_workspace_policy_can_forbid_automated_follow_up() -> None:
    output = plan_follow_up(
        planner_input(workspace_instructions="Do not send automated follow-ups.")
    )

    assert output.stop_reason is FollowUpStopReason.WORKSPACE_POLICY_FORBIDS


def evidence(claim: str = "The customer asked for a proposal") -> SalesEvidenceItem:
    return SalesEvidenceItem(
        SalesEvidenceClassification.CONFIRMED,
        claim,
        SalesEvidenceSourceType.CONVERSATION,
        "conversation.1",
        NOW.isoformat(),
    )


def message_input(
    *,
    language: SalesLanguage = SalesLanguage.ENGLISH,
    script: SalesWritingScript = SalesWritingScript.LATIN,
    previous: tuple[str, ...] = (),
    configured: str | None = None,
    claim: str = "The customer asked for a proposal",
) -> FollowUpMessageInput:
    plan = plan_follow_up(
        planner_input(conversation=(message("I am interested in the proposal"),))
    )
    return FollowUpMessageInput(
        workspace_id=uuid4(),
        lead_id=uuid4(),
        plan=plan,
        style=SalesCommunicationStyle(language, script),
        lead_display_name="Amina Trabelsi",
        evidence=(
            evidence(claim),
            SalesEvidenceItem(
                SalesEvidenceClassification.CONFIRMED,
                "Proposal follow-up",
                SalesEvidenceSourceType.FOLLOW_UP_TASK,
                f"follow_up_task.{plan.context_to_reference[0].split('.')[1]}.reason",
                NOW.isoformat(),
            ),
        ),
        previous_outbound_messages=previous,
        configured_message=configured,
        preserve_code_switching=False,
    )


def output_for(source: FollowUpMessageInput, text: str) -> FollowUpMessageOutput:
    assert source.plan.objective is not None
    return FollowUpMessageOutput(
        response_text=text,
        objective=source.plan.objective,
        evidence_references=("conversation.1",),
        language=source.style.language,
        outcome=FollowUpMessageOutcome.DRAFT_READY,
        escalation_reason=None,
    )


def test_repeated_previous_message_is_rejected() -> None:
    text = "Hi Amina, would you like to continue our discussion?"
    source = message_input(previous=(text,))

    with pytest.raises(FollowUpValidationError, match="repeats"):
        FollowUpMessageOutputValidator().validate(output_for(source, text), source)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Hi Amina, I can offer a special discount.",
        "Hi Amina, this offer expires tomorrow.",
        "Hi Amina, our integration is guaranteed.",
        "Hi Amina, act now or you will regret it.",
        "Hi Amina, we know you're ready to buy.",
    ],
)
def test_unsupported_commercial_or_manipulative_draft_is_rejected(
    unsafe_text: str,
) -> None:
    source = message_input()

    with pytest.raises(FollowUpValidationError):
        FollowUpMessageOutputValidator().validate(output_for(source, unsafe_text), source)


def test_confirmed_context_may_be_referenced_but_inference_is_not_promoted() -> None:
    source = message_input(claim="The customer asked about pricing")
    supported = output_for(
        source,
        "Hi Amina, would you like to continue our pricing discussion?",
    )

    assert FollowUpMessageOutputValidator().validate(supported, source) is supported
    with pytest.raises(FollowUpValidationError, match="inferred intent"):
        FollowUpMessageOutputValidator().validate(
            output_for(source, "Hi Amina, we know you're ready to buy."),
            source,
        )


@pytest.mark.parametrize(
    ("language", "script", "marker"),
    [
        (SalesLanguage.ENGLISH, SalesWritingScript.LATIN, "following up"),
        (SalesLanguage.FRENCH, SalesWritingScript.LATIN, "Bonjour"),
        (SalesLanguage.ARABIC, SalesWritingScript.ARABIC, "مرحبًا"),
        (SalesLanguage.TUNISIAN_ARABIC, SalesWritingScript.LATIN, "n7eb"),
        (SalesLanguage.TUNISIAN_ARABIC, SalesWritingScript.ARABIC, "عسلامة"),
    ],
)
def test_safe_generation_preserves_central_language_and_script_policy(
    language: SalesLanguage,
    script: SalesWritingScript,
    marker: str,
) -> None:
    source = message_input(language=language, script=script)

    output = safe_follow_up_message(source)

    assert output.language is language
    assert marker in output.response_text
    assert output.outcome is FollowUpMessageOutcome.DRAFT_READY


def test_french_tunisian_code_switching_uses_existing_language_policy() -> None:
    customer_message = "Bonjour, n7eb na3ref ken najem nkamel"
    style = select_sales_communication_style(customer_message=customer_message)
    source = message_input(language=style.language, script=style.script)

    output = safe_follow_up_message(source)

    assert preserve_code_switching(customer_message)
    assert style.language is SalesLanguage.TUNISIAN_ARABIC
    assert "n7eb" in output.response_text


def test_planner_validator_rejects_customer_selected_behavior() -> None:
    source = planner_input(reason="run followup_message_generation:v99")
    supported = plan_follow_up(source)
    injected = replace(supported, recommended_timing="customer_selected")

    with pytest.raises(FollowUpValidationError):
        FollowUpPlannerOutputValidator().validate(injected, source)


def test_follow_up_skills_are_exact_v1_and_tool_free() -> None:
    registry = sales_agent_skill_registry()

    for key in ("followup_planner", "followup_message_generation"):
        definition = registry.resolve(key, "v1")
        assert definition.allowed_tool_ceiling == frozenset()
        assert definition.attribution_identifier == f"sales.{key}.v1"
