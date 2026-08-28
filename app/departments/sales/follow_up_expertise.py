"""Governed, provider-neutral expertise for one persisted Sales follow-up."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.core.agent_skill_execution import (
    AgentSkillComponentResolver,
    AgentSkillContractRegistry,
    AgentSkillValidatorRegistry,
    ResolvedAgentSkillComponents,
)
from app.core.agent_skills import AgentSkillDefinition
from app.departments.sales.evidence import SalesEvidenceItem
from app.departments.sales.language_policy import (
    SalesCommunicationStyle,
    detect_sales_language,
    validate_sales_script_consistency,
)
from app.models import LeadStatus, SalesLanguage

FOLLOWUP_PLANNER_KEY = "followup_planner"
FOLLOWUP_MESSAGE_GENERATION_KEY = "followup_message_generation"
FOLLOWUP_EXPERTISE_VERSION = "v1"

FOLLOWUP_PLANNER_INSTRUCTIONS = (
    "Follow-up planner v1 is deterministic and advisory. It may recommend one bounded "
    "follow-up only from the supplied HIRI state. It must stop for opt-out, terminal or "
    "unqualified leads, active human handoff, a newer customer reply, duplicate pending "
    "work, repeated unanswered attempts, satisfied objectives, or a workspace prohibition."
)
FOLLOWUP_MESSAGE_INSTRUCTIONS = (
    "Follow-up message generation v1: Return one JSON object only with response_text, "
    "objective, evidence_references, language, outcome, and escalation_reason. Write a "
    "concise continuation in the selected language and style. Use only supplied evidence. "
    "Never invent discounts, promotions, prices, deadlines, scarcity, urgency, integrations, "
    "guarantees, ROI, testimonials, legal commitments, or availability promises. Do not "
    "repeat a previous message or use guilt, threats, or aggressive closing."
)

_OPT_OUT = re.compile(
    r"(?i)(?:\bdo\s+not\s+(?:contact|message|text|call)\b|"
    r"\bdon['’]?t\s+(?:contact|message|text|call)\s+me\b|"
    r"\bstop\s+(?:contacting|messaging|texting|calling)\s+me\b|"
    r"\bunsubscribe\b|ne\s+me\s+contactez\s+plus|"
    r"ne\s+m['’]envoyez\s+plus\s+de\s+messages|"
    r"لا\s+تتصلوا\s+بي|لا\s+تراسلوني|ما\s+عادش\s+تبعثولي)"
)
_WORKSPACE_FORBIDS = re.compile(
    r"(?i)(?:do\s+not\s+(?:send\s+)?(?:automated\s+)?follow[- ]?ups|"
    r"no\s+(?:automated\s+)?follow[- ]?ups|follow[- ]?ups?\s+(?:are\s+)?forbidden)"
)
_INTEREST = re.compile(
    r"(?i)(?:\bprice|pricing|cost|demo|proposal|quote|interested|"
    r"implementation|integrat|ready\s+to|tell\s+me\s+more|"
    r"prix|tarif|d[ée]monstration|proposition|int[ée]ress[ée]|"
    r"سعر|عرض\s+توضيحي|مهتم|تنفيذ|تكامل)"
)
_GENERIC_REASON = re.compile(
    r"(?i)^\s*(?:follow[- ]?up|check(?:ing)?\s+in|reminder|relance|متابعة)\s*$"
)
_COMMERCIAL_RISK = re.compile(
    r"(?i)(?:\bdiscount|promotion|promo\b|special\s+offer|"
    r"\bguarantee|guaranteed|\bROI\b|return\s+on\s+investment|"
    r"\bdeadline|expires?|limited\s+time|today\s+only|last\s+chance|"
    r"only\s+\d+\s+(?:left|spots?)|\bintegrat(?:e|es|ed|ion|ions)\b|"
    r"remise|promotion|garanti|date\s+limite|offre\s+limit[ée]e|"
    r"خصم|عرض\s+خاص|مضمون|موعد\s+نهائي|لفترة\s+محدودة|تكامل)"
)
_MANIPULATION = re.compile(
    r"(?i)(?:\bact\s+now|\burgent(?:ly)?\b|\bimmediately\b|"
    r"you['’]ll\s+(?:miss|regret)|don['’]t\s+miss\s+out|"
    r"i['’]m\s+disappointed|you\s+owe|why\s+haven['’]t\s+you|"
    r"derni[eè]re\s+chance|agissez\s+maintenant|"
    r"الفرصة\s+الأخيرة|تحرك\s+الآن|ستندم)"
)
_FALSE_INTENT = re.compile(
    r"(?i)(?:we\s+know\s+you(?:['’]re|\s+are)\s+ready|"
    r"you(?:['’]re|\s+are)\s+ready\s+to\s+buy|"
    r"nous\s+savons\s+que\s+vous\s+[êe]tes\s+pr[êe]t|"
    r"نعلم\s+أنك\s+مستعد\s+للشراء)"
)
_PRICING = re.compile(r"(?i)(?:\bprice|pricing|cost|prix|tarif|سعر)")
_NUMBER = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])")


class FollowUpPlanOutcome(StrEnum):
    FOLLOW_UP_RECOMMENDED = "follow_up_recommended"
    NO_FOLLOW_UP = "no_follow_up"
    HUMAN_PAUSE = "human_pause"


class FollowUpStopReason(StrEnum):
    CUSTOMER_OPTED_OUT = "customer_opted_out"
    LEAD_STATUS_WON = "lead_status_won"
    LEAD_STATUS_LOST = "lead_status_lost"
    LEAD_STATUS_UNQUALIFIED = "lead_status_unqualified"
    ACTIVE_HUMAN_HANDOFF = "active_human_handoff"
    NEWER_CUSTOMER_REPLY = "newer_customer_reply"
    DUPLICATE_PENDING_FOLLOW_UP = "duplicate_pending_follow_up"
    OBJECTIVE_ALREADY_ATTEMPTED = "objective_already_attempted"
    EXCESSIVE_UNANSWERED_FOLLOW_UP = "excessive_unanswered_follow_up"
    INSUFFICIENT_ENGAGEMENT = "insufficient_engagement"
    WORKSPACE_POLICY_FORBIDS = "workspace_policy_forbids"


def terminal_follow_up_stop_reason(
    lead_status: LeadStatus,
) -> FollowUpStopReason | None:
    """Return the canonical follow-up stop reason for a terminal Lead state."""

    return {
        LeadStatus.WON: FollowUpStopReason.LEAD_STATUS_WON,
        LeadStatus.LOST: FollowUpStopReason.LEAD_STATUS_LOST,
        LeadStatus.UNQUALIFIED: FollowUpStopReason.LEAD_STATUS_UNQUALIFIED,
    }.get(lead_status)


class FollowUpMessageOutcome(StrEnum):
    DRAFT_READY = "draft_ready"
    ESCALATION_REQUIRED = "escalation_required"


class FollowUpValidationOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class FollowUpContractError(ValueError):
    """Raised when generated follow-up output violates its typed shape."""


class FollowUpValidationError(ValueError):
    """Raised when follow-up output exceeds the authoritative source."""


@dataclass(frozen=True, slots=True)
class FollowUpConversationMessage:
    reference: str
    direction: str
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PriorFollowUp:
    task_id: UUID
    reason: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FollowUpPlannerInput:
    workspace_id: UUID
    lead_id: UUID
    task_id: UUID
    lead_status: LeadStatus
    reason: str
    due_at: datetime
    task_created_at: datetime
    conversation: tuple[FollowUpConversationMessage, ...]
    prior_follow_ups: tuple[PriorFollowUp, ...]
    active_handoff: bool
    workspace_instructions: str | None


@dataclass(frozen=True, slots=True)
class FollowUpPlanOutput:
    should_follow_up: bool
    reason: str
    objective: str | None
    recommended_timing: str
    not_before: str
    context_to_reference: tuple[str, ...]
    stop_reason: FollowUpStopReason | None
    outcome: FollowUpPlanOutcome

    def as_dict(self) -> dict[str, object]:
        return {
            "should_follow_up": self.should_follow_up,
            "reason": self.reason,
            "objective": self.objective,
            "recommended_timing": self.recommended_timing,
            "not_before": self.not_before,
            "context_to_reference": list(self.context_to_reference),
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "outcome": self.outcome.value,
        }


@dataclass(frozen=True, slots=True)
class FollowUpMessageInput:
    workspace_id: UUID
    lead_id: UUID
    plan: FollowUpPlanOutput
    style: SalesCommunicationStyle
    lead_display_name: str
    evidence: tuple[SalesEvidenceItem, ...]
    previous_outbound_messages: tuple[str, ...]
    configured_message: str | None
    preserve_code_switching: bool

    def evidence_references(self) -> frozenset[str]:
        return frozenset(
            item.source_reference
            for item in self.evidence
            if item.source_reference is not None
        )

    def evidence_corpus(self) -> str:
        return "\n".join(item.claim for item in self.evidence)


@dataclass(frozen=True, slots=True)
class FollowUpMessageOutput:
    response_text: str
    objective: str
    evidence_references: tuple[str, ...]
    language: SalesLanguage
    outcome: FollowUpMessageOutcome
    escalation_reason: str | None

    @classmethod
    def from_json(cls, raw: str) -> FollowUpMessageOutput:
        value = _json_object(raw)
        _require_fields(
            value,
            {
                "response_text",
                "objective",
                "evidence_references",
                "language",
                "outcome",
                "escalation_reason",
            },
        )
        return cls(
            response_text=_required_text(value["response_text"], "response_text", 700),
            objective=_required_text(value["objective"], "objective", 300),
            evidence_references=_text_list(
                value["evidence_references"], "evidence_references", 20
            ),
            language=_enum(SalesLanguage, value["language"], "language"),
            outcome=_enum(FollowUpMessageOutcome, value["outcome"], "outcome"),
            escalation_reason=_optional_text(
                value["escalation_reason"], "escalation_reason", 200
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "response_text": self.response_text,
            "objective": self.objective,
            "evidence_references": list(self.evidence_references),
            "language": self.language.value,
            "outcome": self.outcome.value,
            "escalation_reason": self.escalation_reason,
        }


@dataclass(frozen=True, slots=True)
class FollowUpSkillExecutionResult:
    outcome: StrEnum
    validation_outcome: FollowUpValidationOutcome
    validation_reason: str
    ai_invoked: bool
    structured_result: dict[str, object]


class FollowUpPlannerOutputValidator:
    def validate(
        self,
        value: object,
        source: FollowUpPlannerInput | None = None,
    ) -> FollowUpPlanOutput:
        if not isinstance(value, FollowUpPlanOutput) or source is None:
            raise FollowUpValidationError("Follow-up planning requires typed input")
        if value != plan_follow_up(source):
            raise FollowUpValidationError("Follow-up plan is not supported by HIRI state")
        return value


class FollowUpMessageOutputValidator:
    def validate(
        self,
        value: object,
        source: FollowUpMessageInput | None = None,
    ) -> FollowUpMessageOutput:
        if not isinstance(value, FollowUpMessageOutput) or source is None:
            raise FollowUpValidationError("Follow-up generation requires typed input")
        if not source.plan.should_follow_up:
            raise FollowUpValidationError("A stopped plan cannot generate a message")
        if value.objective != source.plan.objective:
            raise FollowUpValidationError("Follow-up objective was changed")
        if value.language is not source.style.language:
            raise FollowUpValidationError("Follow-up language does not match policy")
        if value.outcome is FollowUpMessageOutcome.DRAFT_READY:
            if value.escalation_reason is not None:
                raise FollowUpValidationError("Ready draft cannot contain escalation")
        elif value.escalation_reason is None:
            raise FollowUpValidationError("Escalation outcome requires a reason")
        references = set(value.evidence_references)
        if not references or not references.issubset(source.evidence_references()):
            raise FollowUpValidationError("Follow-up evidence references are unsupported")
        normalized = _normalized_message(value.response_text)
        if any(
            normalized == _normalized_message(previous)
            for previous in source.previous_outbound_messages
        ):
            raise FollowUpValidationError("Follow-up repeats a previous message")
        if _COMMERCIAL_RISK.search(value.response_text):
            raise FollowUpValidationError("Follow-up contains an unsupported commercial claim")
        if _MANIPULATION.search(value.response_text):
            raise FollowUpValidationError("Follow-up contains manipulative pressure")
        if _FALSE_INTENT.search(value.response_text):
            raise FollowUpValidationError("Follow-up promotes inferred intent to fact")
        detected_language = detect_sales_language(value.response_text)
        if detected_language is not None and detected_language is not source.style.language:
            raise FollowUpValidationError("Follow-up text violates language continuity")
        if _PRICING.search(value.response_text) and not _PRICING.search(
            source.evidence_corpus()
        ):
            raise FollowUpValidationError("Follow-up introduces unsupported pricing context")
        if not set(_NUMBER.findall(value.response_text)).issubset(
            set(_NUMBER.findall(source.evidence_corpus()))
        ):
            raise FollowUpValidationError("Follow-up contains an unsupported number")
        script = validate_sales_script_consistency(
            text=value.response_text,
            style=source.style,
        )
        if not script.is_consistent:
            raise FollowUpValidationError("Follow-up writing script violates language policy")
        return value


def plan_follow_up(source: FollowUpPlannerInput) -> FollowUpPlanOutput:
    inbound = tuple(
        message for message in source.conversation if message.direction == "inbound"
    )
    inbound_corpus = "\n".join(message.content for message in inbound)
    if _OPT_OUT.search(inbound_corpus):
        return _stopped_plan(source, FollowUpStopReason.CUSTOMER_OPTED_OUT)
    terminal = terminal_follow_up_stop_reason(source.lead_status)
    if terminal is not None:
        return _stopped_plan(source, terminal)
    if source.active_handoff:
        return _stopped_plan(
            source,
            FollowUpStopReason.ACTIVE_HUMAN_HANDOFF,
            outcome=FollowUpPlanOutcome.HUMAN_PAUSE,
        )
    if source.workspace_instructions and _WORKSPACE_FORBIDS.search(
        source.workspace_instructions
    ):
        return _stopped_plan(source, FollowUpStopReason.WORKSPACE_POLICY_FORBIDS)
    if any(item.status == "pending" for item in source.prior_follow_ups):
        return _stopped_plan(source, FollowUpStopReason.DUPLICATE_PENDING_FOLLOW_UP)
    if any(message.created_at > source.task_created_at for message in inbound):
        return _stopped_plan(source, FollowUpStopReason.NEWER_CUSTOMER_REPLY)
    completed = tuple(item for item in source.prior_follow_ups if item.status == "completed")
    reason = source.reason.strip().casefold()
    if any(
        item.reason.strip().casefold() == reason
        and not any(message.created_at > item.created_at for message in inbound)
        for item in completed
    ):
        return _stopped_plan(source, FollowUpStopReason.OBJECTIVE_ALREADY_ATTEMPTED)
    last_inbound_at = max((message.created_at for message in inbound), default=None)
    unanswered = tuple(
        item
        for item in completed
        if last_inbound_at is None or item.created_at > last_inbound_at
    )
    if len(unanswered) >= 2:
        return _stopped_plan(source, FollowUpStopReason.EXCESSIVE_UNANSWERED_FOLLOW_UP)
    supported = source.lead_status in {
        LeadStatus.QUALIFIED,
        LeadStatus.ENGAGED,
        LeadStatus.PROPOSAL,
        LeadStatus.NEGOTIATION,
    } or _INTEREST.search(f"{source.reason}\n{inbound_corpus}")
    if not supported and _GENERIC_REASON.fullmatch(source.reason):
        return _stopped_plan(source, FollowUpStopReason.INSUFFICIENT_ENGAGEMENT)

    context = [f"follow_up_task.{source.task_id}.reason"]
    if inbound:
        context.append(inbound[-1].reference)
    return FollowUpPlanOutput(
        should_follow_up=True,
        reason="existing_due_follow_up_is_supported",
        objective=f"Continue the existing conversation about {source.reason.strip()}",
        recommended_timing="existing_scheduled_due_time",
        not_before=source.due_at.isoformat(),
        context_to_reference=tuple(context),
        stop_reason=None,
        outcome=FollowUpPlanOutcome.FOLLOW_UP_RECOMMENDED,
    )


def safe_follow_up_message(source: FollowUpMessageInput) -> FollowUpMessageOutput:
    if not source.plan.should_follow_up or source.plan.objective is None:
        raise FollowUpValidationError("A stopped plan cannot generate a message")
    references = _message_references(source)
    candidates = _localized_candidates(source)
    for candidate in candidates:
        output = FollowUpMessageOutput(
            response_text=candidate,
            objective=source.plan.objective,
            evidence_references=references,
            language=source.style.language,
            outcome=FollowUpMessageOutcome.DRAFT_READY,
            escalation_reason=None,
        )
        try:
            return FollowUpMessageOutputValidator().validate(output, source)
        except FollowUpValidationError:
            continue
    return FollowUpMessageOutput(
        response_text=_review_message(source.style.language),
        objective=source.plan.objective,
        evidence_references=references,
        language=source.style.language,
        outcome=FollowUpMessageOutcome.ESCALATION_REQUIRED,
        escalation_reason="safe_non_repeating_draft_unavailable",
    )


def configured_follow_up_message(source: FollowUpMessageInput) -> FollowUpMessageOutput:
    if source.configured_message is None or source.plan.objective is None:
        raise FollowUpValidationError("Configured follow-up draft is unavailable")
    return FollowUpMessageOutput(
        response_text=source.configured_message,
        objective=source.plan.objective,
        evidence_references=_message_references(source),
        language=source.style.language,
        outcome=FollowUpMessageOutcome.DRAFT_READY,
        escalation_reason=None,
    )


def followup_planner_components(
    definition: AgentSkillDefinition,
) -> ResolvedAgentSkillComponents:
    return _components(
        definition,
        FollowUpPlannerInput,
        FollowUpPlanOutput,
        FollowUpPlannerOutputValidator(),
    )


def followup_message_components(
    definition: AgentSkillDefinition,
) -> ResolvedAgentSkillComponents:
    return _components(
        definition,
        FollowUpMessageInput,
        FollowUpMessageOutput,
        FollowUpMessageOutputValidator(),
    )


def planner_execution_result(output: FollowUpPlanOutput) -> FollowUpSkillExecutionResult:
    return FollowUpSkillExecutionResult(
        outcome=output.outcome,
        validation_outcome=FollowUpValidationOutcome.ACCEPTED,
        validation_reason="deterministic_hiri_state_validated",
        ai_invoked=False,
        structured_result=output.as_dict(),
    )


def message_execution_result(
    output: FollowUpMessageOutput,
    *,
    ai_invoked: bool,
    rejected: bool = False,
    validation_reason: str = "grounded_output_accepted",
) -> FollowUpSkillExecutionResult:
    return FollowUpSkillExecutionResult(
        outcome=output.outcome,
        validation_outcome=(
            FollowUpValidationOutcome.REJECTED
            if rejected
            else FollowUpValidationOutcome.ACCEPTED
        ),
        validation_reason=validation_reason,
        ai_invoked=ai_invoked,
        structured_result=output.as_dict(),
    )


def _stopped_plan(
    source: FollowUpPlannerInput,
    stop_reason: FollowUpStopReason,
    *,
    outcome: FollowUpPlanOutcome = FollowUpPlanOutcome.NO_FOLLOW_UP,
) -> FollowUpPlanOutput:
    return FollowUpPlanOutput(
        should_follow_up=False,
        reason=stop_reason.value,
        objective=None,
        recommended_timing="do_not_schedule",
        not_before=source.due_at.isoformat(),
        context_to_reference=(),
        stop_reason=stop_reason,
        outcome=outcome,
    )


def _localized_candidates(source: FollowUpMessageInput) -> tuple[str, ...]:
    name = source.lead_display_name.strip().split()[0] if source.lead_display_name.strip() else ""
    if source.style.language is SalesLanguage.FRENCH:
        generated = (
            (
                f"Bonjour {name}, je reviens vers vous au sujet de notre échange. "
                "Souhaitez-vous poursuivre la discussion ?"
            ),
            (
                f"Bonjour {name}, petit suivi concernant notre dernier échange. "
                "Je reste disponible si vous souhaitez continuer."
            ),
        )
    elif source.style.language is SalesLanguage.ARABIC:
        generated = (
            f"مرحبًا {name}، أتابع معك بخصوص حديثنا السابق. هل ترغب في مواصلة النقاش؟",
            f"مرحبًا {name}، أود متابعة حديثنا الأخير. أنا متاح إذا رغبت في المتابعة.",
        )
    elif source.style.language is SalesLanguage.TUNISIAN_ARABIC:
        if source.style.script.value == "arabic":
            generated = (
                f"عسلامة {name}، نحب نتابع معاك على حديثنا اللي فات. تحب نكملوا النقاش؟",
                f"عسلامة {name}، نرجعلك بخصوص كلامنا الأخير. أنا موجود كان تحب نكملوا.",
            )
        else:
            generated = (
                (
                    f"Aslema {name}, n7eb netba3 m3ak 3la 7keyetna elli fetet. "
                    "T7eb nkamlou el discussion?"
                ),
                (
                    f"Aslema {name}, narja3lek bennesba l klemna elli fet. "
                    "Ena mawjoud ken t7eb nkamlou."
                ),
            )
    else:
        generated = (
            (
                f"Hi {name}, I’m following up on our earlier conversation. "
                "Would you like to continue the discussion?"
            ),
            (
                f"Hi {name}, a quick follow-up on our last conversation. "
                "I’m available if you’d like to continue."
            ),
        )
    return generated


def _message_references(source: FollowUpMessageInput) -> tuple[str, ...]:
    references = tuple(
        reference
        for reference in source.plan.context_to_reference
        if reference in source.evidence_references()
    )
    return references or tuple(sorted(source.evidence_references()))[:1]


def _review_message(language: SalesLanguage) -> str:
    return {
        SalesLanguage.ENGLISH: "A team member should review the next follow-up.",
        SalesLanguage.FRENCH: "Un membre de l’équipe doit examiner la prochaine relance.",
        SalesLanguage.ARABIC: "يجب أن يراجع أحد أعضاء الفريق رسالة المتابعة التالية.",
        SalesLanguage.TUNISIAN_ARABIC: "يلزم واحد من الفريق يراجع المتابعة الجاية.",
    }[language]


def _components(
    definition: AgentSkillDefinition,
    input_contract: type[object],
    output_contract: type[object],
    validator: FollowUpPlannerOutputValidator | FollowUpMessageOutputValidator,
) -> ResolvedAgentSkillComponents:
    return AgentSkillComponentResolver(
        AgentSkillContractRegistry(
            (
                (definition.input_contract, input_contract),
                (definition.output_contract, output_contract),
            )
        ),
        AgentSkillValidatorRegistry(((definition.validator, validator),)),
    ).resolve(definition)


def _normalized_message(value: str) -> str:
    return " ".join(value.casefold().split())


def _json_object(raw: str) -> dict[str, object]:
    normalized = raw.strip()
    if normalized.startswith("```"):
        normalized = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
    try:
        value = json.loads(normalized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise FollowUpContractError("Follow-up output must be one JSON object") from exc
    if not isinstance(value, dict):
        raise FollowUpContractError("Follow-up output must be an object")
    return value


def _require_fields(value: dict[str, object], required: set[str]) -> None:
    if set(value) != required:
        raise FollowUpContractError("Follow-up output fields are invalid")


def _text_list(value: object, field: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise FollowUpContractError(f"{field} must be bounded")
    return tuple(_required_text(item, field, 200) for item in value)


def _required_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise FollowUpContractError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise FollowUpContractError(f"{field} is invalid")
    return normalized


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, field, maximum)


def _enum(enum_type: type[StrEnum], value: object, field: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise FollowUpContractError(f"{field} is invalid") from exc
