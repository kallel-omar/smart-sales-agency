"""Governed core Sales conversation AgentSkills for Task 296E.

This module owns only bounded selection, transient contracts, deterministic
validation, and safe fallbacks for discovery, objection handling, and buyer
indecision. Persistence, authorization, AI routing, and delivery stay in their
existing HIRI boundaries.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from uuid import UUID

from app.core.agent_skill_execution import (
    AgentSkillComponentResolver,
    AgentSkillContractRegistry,
    AgentSkillValidatorRegistry,
    ResolvedAgentSkillComponents,
)
from app.core.agent_skills import AgentSkillDefinition
from app.departments.sales.evidence import SalesEvidenceClassification
from app.departments.sales.language_policy import (
    SalesCommunicationStyle,
    detect_sales_language,
    validate_sales_script_consistency,
)
from app.departments.sales.pricing_explanation import is_pricing_explanation_turn
from app.departments.sales.prompt_composition import SalesProductContext
from app.models import SalesLanguage, SalesStage, SalesWritingScript

NEEDS_DISCOVERY_KEY = "needs_discovery"
OBJECTION_HANDLING_KEY = "objection_handling"
BUYER_INDECISION_KEY = "buyer_indecision"
CONVERSATION_EXPERTISE_VERSION = "v1"

_SUPPORTED_KEYS = frozenset({NEEDS_DISCOVERY_KEY, OBJECTION_HANDLING_KEY, BUYER_INDECISION_KEY})

NEEDS_DISCOVERY_INSTRUCTIONS = (
    "Needs discovery skill v1: Reuse facts the customer already supplied. Identify "
    "the single most important missing detail and ask at most one natural, high-value "
    "question. Do not interrogate, repeat a known question, invent a pain point, or "
    "continue discovery when context is sufficient or buying intent is clear. Return "
    "one JSON object only with keys response_text, discovered_facts, inferred_needs, "
    "missing_information, next_step, outcome, and language. Each fact item has fact "
    "and evidence; evidence must be confirmed, inference, or unknown. outcome must be "
    "continue_discovery, sufficient_context, or handoff_required."
)

OBJECTION_HANDLING_INSTRUCTIONS = (
    "Objection handling skill v1: Acknowledge the actual concern without arguing or "
    "pressuring. Use only authoritative product and customer facts, then address the "
    "concern concisely and optionally ask one useful question. Never invent ROI, "
    "testimonials, integrations, guarantees, scarcity, discounts, or commercial "
    "authority. Return one JSON object only with keys response_text, objection_type, "
    "evidence_used, unresolved_points, next_step, escalation_reason, outcome, and "
    "language. outcome must be addressed, needs_clarification, commercial_escalation, "
    "or human_handoff."
)

BUYER_INDECISION_INSTRUCTIONS = (
    "Buyer indecision skill v1: Treat hesitation as uncertainty, not an objection to "
    "attack. Reduce complexity, use verified facts only, and recommend one useful next "
    "decision step without pressure, scarcity, or artificial urgency. A follow-up may "
    "be recommended but must never be scheduled or sent. Return one JSON object only "
    "with keys response_text, blocker_type, known_decision_factors, "
    "missing_decision_information, recommended_next_step, outcome, escalation_reason, "
    "and language. outcome must be supported, needs_clarification, wait_recommended, "
    "or human_handoff."
)

_OBJECTION_SIGNAL = re.compile(
    r"(?i)(?:\btoo\s+expensive\b|\bexpensive\b|\btoo\s+much\b|"
    r"\balready\s+(?:use|using|have)\b|\bworried\b|\bconcerned\b|"
    r"\b(?:don['’]?t|do\s+not)\s+have\s+time\b|"
    r"\b(?:won['’]?t|will\s+not|doesn['’]?t)\s+work\b|"
    r"\b(?:don['’]?t|do\s+not)\s+think\b.{0,30}\bwork\b|"
    r"\bnot\s+interested\b|\bguarantee\b|\bpromise\b|\b(?:gdpr|hipaa|legal|compliance)\b|"
    r"\btrop\s+ch(?:er|ère)\b|"
    r"\bd[ée]j[àa]\s+(?:un|une|utilis)|\binqui[eè]t|\bpas\s+le\s+temps\b|"
    r"\bne\s+(?:va|peut)\s+pas\s+(?:marcher|fonctionner)\b|\bgarantie\b|\bconformit[ée]\b|"
    r"غالي|مكلف|نستخدم\s+(?:حاليا|بالفعل)|قلق|لن\s+ينجح|لا\s+يعمل|ضمان|امتثال)"
)
_INDECISION_SIGNAL = re.compile(
    r"(?i)(?:\bneed\s+to\s+think\b|\bnot\s+sure\b|\bunsure\b|\bundecided\b|"
    r"\bcompare\s+(?:the\s+)?options?\b|\bseems?\s+good\s+but\b|"
    r"\btoo\s+many\s+options?\b|\bcan['’]?t\s+decide\b|\bhesitat(?:e|ing)\b|"
    r"\bdois\s+r[ée]fl[ée]chir\b|\bpas\s+s[uû]r\b|\bj['’]?h[ée]site\b|"
    r"\bcomparer\s+(?:les\s+)?options?\b|\btrop\s+d['’]options\b|"
    r"أحتاج\s+أن\s+أفكر|لا\s+أزال\s+غير\s+متأكد|غير\s+متأكد|متردد|"
    r"أقارن\s+(?:بين\s+)?الخيارات|خيارات\s+كثيرة|"
    r"\bn(?:7|h)eb\s+n(?:5|kh)amem\b|\bmechi\s+sure\b|\bmazelt\s+moch\s+met2aked\b)"
)
_DISCOVERY_SIGNAL = re.compile(
    r"(?i)(?:\bwe\s+(?:need|receive|get|handle|struggle|can['’]?t)\b|"
    r"\bour\s+(?:problem|challenge|team|business|leads?|messages?|conversations?)\b|"
    r"\blooking\s+for\b|\bneed\s+help\b|\bcan['’]?t\s+(?:answer|reply|follow)\b|"
    r"\bnous\s+(?:avons|recevons|g[ée]rons|cherchons)\b|\bnotre\s+(?:probl[eè]me|[ée]quipe)\b|"
    r"\bbesoin\s+d['’]aide\b|\bon\s+re[cç]oit\b|"
    r"نحتاج|لدينا\s+(?:مشكلة|الكثير)|نستقبل|لا\s+نستطيع\s+(?:الرد|المتابعة)|"
    r"عنا\s+(?:مشكلة|برشا)|يجينا\s+برشا|مانجموش\s+(?:نجاوبو|نتبعو)|"
    r"\b3anna\b|\bbarsha\s+(?:messages?|leads?)\b|\bmanajmouch\b)"
)
_BUYING_INTENT_SIGNAL = re.compile(
    r"(?i)(?:\bready\s+to\s+(?:buy|start|sign)\b|\bwant\s+to\s+(?:buy|start|sign)\b|"
    r"\bhow\s+do\s+(?:i|we)\s+(?:buy|start|sign\s+up)\b|\bplace\s+an\s+order\b|"
    r"\bpr[êe]t\s+[àa]\s+(?:acheter|commencer|signer)\b|\bje\s+veux\s+(?:acheter|commencer)\b|"
    r"جاهز\s+(?:للشراء|للبدء)|أريد\s+(?:الشراء|البدء)|كيف\s+أبدأ|"
    r"\bnheb\s+(?:nechri|nebda)\b)"
)
_GUARANTEE_REQUEST = re.compile(
    r"(?i)(?:\bguarantee(?:d)?\b|\bpromise\b|\b100\s*%\b|"
    r"\b(?:gdpr|hipaa|legal|compliance)\b|\bgaranti(?:e|r)\b|\bconformit[ée]\b|"
    r"ضمان|مضمون|امتثال)"
)
_INTEGRATION_TERM = re.compile(
    r"(?i)\b(?:salesforce|hubspot|zapier|slack|crm|erp|api|integration)\b|تكامل|يدمج"
)
_UNSAFE_COMMERCIAL_REPLY = re.compile(
    r"(?i)(?:\b(?:i|we)\s+(?:guarantee|promise)\b|\bguaranteed\s+(?:roi|results?|success)\b|"
    r"\b\d+(?:[.,]\d+)?\s*%\s+(?:roi|return|increase|improvement|off)\b|"
    r"\b(?:customer|client)s?\s+(?:achieved|reported|saw)\b|\bcase\s+study\b|"
    r"\bonly\s+\d+\s+(?:spots?|places?|left)\b|\bact\s+now\b|\bexpires?\s+(?:today|soon)\b|"
    r"\b(?:i|we)\s+can\s+(?:give|offer|apply)\b.{0,35}\bdiscount\b|"
    r"\bje\s+(?:garantis|promets)\b|\br[ée]sultat\s+garanti\b|\bderni[eè]re\s+chance\b|"
    r"\b(?:remise|r[ée]duction)\s+(?:accord[ée]e|garantie)\b|"
    r"أضمن|نضمن|نتيجة\s+مضمونة|فرصة\s+أخيرة|خصم\s+مضمون)"
)
_PRESSURE_REPLY = re.compile(
    r"(?i)(?:\byou\s+(?:must|need\s+to)\s+(?:decide|buy|act)\s+(?:now|today)\b|"
    r"\bdon['’]?t\s+miss\s+out\b|\blast\s+chance\b|\bno\s+reason\s+to\s+wait\b|"
    r"\bvous\s+devez\s+d[ée]cider\s+(?:maintenant|aujourd['’]hui)\b|"
    r"يجب\s+أن\s+تقرر\s+الآن|لا\s+تنتظر)"
)
_AUTOMATIC_FOLLOW_UP_REPLY = re.compile(
    r"(?i)(?:\b(?:i|we)(?:'ll|\s+will)\s+(?:schedule|send)\s+(?:a\s+)?follow[ -]?up\b|"
    r"\b(?:i|we)(?:'ve|\s+have)\s+(?:scheduled|booked)\b|"
    r"\bje\s+(?:vais|viens\s+de)\s+(?:planifier|programmer)\b|"
    r"سأقوم\s+بجدولة|تمت\s+الجدولة)"
)
_INVENTED_CUSTOMER_ASSERTION = re.compile(
    r"(?i)(?:\byour\s+(?:main\s+)?(?:pain|problem|challenge|need)\s+is\b|"
    r"\byou\s+(?:clearly\s+)?struggle\s+with\b|"
    r"\bvotre\s+(?:principal\s+)?(?:probl[eè]me|besoin|d[ée]fi)\s+est\b|"
    r"مشكلتكم\s+(?:الرئيسية\s+)?هي|احتياجكم\s+هو)"
)
_ASSERTED_PRODUCT_CLAIM = re.compile(
    r"(?i)(?:\b(?:it|we|the\s+product|hiri)\s+(?:includes?|supports?|integrates?|guarantees?|provides?|works?\s+with)\b|"
    r"\b(?:le\s+produit|hiri)\s+(?:inclut|int[eè]gre|garantit|fournit)\b|"
    r"HIRI\s+(?:يشمل|يدعم|يضمن|يوفر))"
)
_NUMBER = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])")


class ConversationSkillValidationOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class NeedsDiscoveryOutcome(StrEnum):
    CONTINUE_DISCOVERY = "continue_discovery"
    SUFFICIENT_CONTEXT = "sufficient_context"
    HANDOFF_REQUIRED = "handoff_required"


class DiscoveryNextStep(StrEnum):
    ASK_ONE_QUESTION = "ask_one_question"
    PROCEED = "proceed"
    HUMAN_HANDOFF = "human_handoff"


class ObjectionType(StrEnum):
    PRICE_VALUE = "price_value"
    EXISTING_SOLUTION = "existing_solution"
    ACCURACY_RISK = "accuracy_risk"
    IMPLEMENTATION_EFFORT = "implementation_effort"
    BUSINESS_FIT = "business_fit"
    INTEGRATION = "integration"
    GUARANTEE = "guarantee"
    OTHER = "other"


class ObjectionOutcome(StrEnum):
    ADDRESSED = "addressed"
    NEEDS_CLARIFICATION = "needs_clarification"
    COMMERCIAL_ESCALATION = "commercial_escalation"
    HUMAN_HANDOFF = "human_handoff"


class ObjectionNextStep(StrEnum):
    CLARIFY_CONCERN = "clarify_concern"
    EXPLAIN_VERIFIED_VALUE = "explain_verified_value"
    HUMAN_REVIEW = "human_review"
    WAIT = "wait"


class BuyerBlockerType(StrEnum):
    UNKNOWN = "unknown"
    COMPARISON = "comparison"
    RISK = "risk"
    COMPLEXITY = "complexity"
    TIMING = "timing"


class BuyerIndecisionOutcome(StrEnum):
    SUPPORTED = "supported"
    NEEDS_CLARIFICATION = "needs_clarification"
    WAIT_RECOMMENDED = "wait_recommended"
    HUMAN_HANDOFF = "human_handoff"


class BuyerNextStep(StrEnum):
    CLARIFY_CONCERN = "clarify_concern"
    SUMMARIZE_FACTORS = "summarize_factors"
    ANSWER_QUESTION = "answer_question"
    HUMAN_DISCUSSION = "human_discussion"
    WAIT = "wait"
    FOLLOW_UP_LATER = "follow_up_later"


class ConversationExpertiseContractError(ValueError):
    """Raised when generated core-expertise output violates its typed contract."""


class ConversationExpertiseValidationError(ValueError):
    """Raised when typed core-expertise output is unsafe or ungrounded."""


@dataclass(frozen=True, slots=True)
class SalesEvidenceFact:
    fact: str
    evidence: SalesEvidenceClassification

    @classmethod
    def from_value(cls, value: object) -> SalesEvidenceFact:
        if not isinstance(value, dict) or set(value) != {"fact", "evidence"}:
            raise ConversationExpertiseContractError("Evidence fact fields are invalid")
        fact = _required_text(value.get("fact"), "fact", 500)
        try:
            evidence = SalesEvidenceClassification(value.get("evidence"))
        except (TypeError, ValueError) as exc:
            raise ConversationExpertiseContractError("Evidence classification is invalid") from exc
        return cls(fact=fact, evidence=evidence)


@dataclass(frozen=True, slots=True)
class ConversationExpertiseMessage:
    direction: str
    content: str


@dataclass(frozen=True, slots=True)
class ConversationExpertiseInput:
    workspace_id: UUID
    customer_message: str
    communication_channel: str | None
    conversation_context: tuple[ConversationExpertiseMessage, ...]
    sales_stage: SalesStage
    lead_facts: tuple[SalesEvidenceFact, ...]
    products: tuple[SalesProductContext, ...]
    language: SalesLanguage
    script: SalesWritingScript
    preserve_code_switching: bool
    workspace_instructions: str | None = None


@dataclass(frozen=True, slots=True)
class NeedsDiscoveryOutput:
    response_text: str
    discovered_facts: tuple[SalesEvidenceFact, ...]
    inferred_needs: tuple[SalesEvidenceFact, ...]
    missing_information: tuple[SalesEvidenceFact, ...]
    next_step: DiscoveryNextStep
    outcome: NeedsDiscoveryOutcome
    language: SalesLanguage

    @classmethod
    def from_json(cls, raw: str) -> NeedsDiscoveryOutput:
        value = _json_object(raw)
        required = {
            "response_text",
            "discovered_facts",
            "inferred_needs",
            "missing_information",
            "next_step",
            "outcome",
            "language",
        }
        _require_exact_fields(value, required)
        return cls(
            response_text=_required_text(value["response_text"], "response_text", 4_000),
            discovered_facts=_fact_list(value["discovered_facts"]),
            inferred_needs=_fact_list(value["inferred_needs"]),
            missing_information=_fact_list(value["missing_information"]),
            next_step=_enum_value(DiscoveryNextStep, value["next_step"], "next_step"),
            outcome=_enum_value(NeedsDiscoveryOutcome, value["outcome"], "outcome"),
            language=_enum_value(SalesLanguage, value["language"], "language"),
        )


@dataclass(frozen=True, slots=True)
class ObjectionHandlingOutput:
    response_text: str
    objection_type: ObjectionType
    evidence_used: tuple[SalesEvidenceFact, ...]
    unresolved_points: tuple[str, ...]
    next_step: ObjectionNextStep
    escalation_reason: str | None
    outcome: ObjectionOutcome
    language: SalesLanguage

    @classmethod
    def from_json(cls, raw: str) -> ObjectionHandlingOutput:
        value = _json_object(raw)
        required = {
            "response_text",
            "objection_type",
            "evidence_used",
            "unresolved_points",
            "next_step",
            "escalation_reason",
            "outcome",
            "language",
        }
        _require_exact_fields(value, required)
        return cls(
            response_text=_required_text(value["response_text"], "response_text", 4_000),
            objection_type=_enum_value(ObjectionType, value["objection_type"], "objection_type"),
            evidence_used=_fact_list(value["evidence_used"]),
            unresolved_points=_text_list(value["unresolved_points"], "unresolved_points"),
            next_step=_enum_value(ObjectionNextStep, value["next_step"], "next_step"),
            escalation_reason=_optional_text(value["escalation_reason"], "escalation_reason", 500),
            outcome=_enum_value(ObjectionOutcome, value["outcome"], "outcome"),
            language=_enum_value(SalesLanguage, value["language"], "language"),
        )


@dataclass(frozen=True, slots=True)
class BuyerIndecisionOutput:
    response_text: str
    blocker_type: BuyerBlockerType
    known_decision_factors: tuple[SalesEvidenceFact, ...]
    missing_decision_information: tuple[SalesEvidenceFact, ...]
    recommended_next_step: BuyerNextStep
    outcome: BuyerIndecisionOutcome
    escalation_reason: str | None
    language: SalesLanguage

    @classmethod
    def from_json(cls, raw: str) -> BuyerIndecisionOutput:
        value = _json_object(raw)
        required = {
            "response_text",
            "blocker_type",
            "known_decision_factors",
            "missing_decision_information",
            "recommended_next_step",
            "outcome",
            "escalation_reason",
            "language",
        }
        _require_exact_fields(value, required)
        return cls(
            response_text=_required_text(value["response_text"], "response_text", 4_000),
            blocker_type=_enum_value(BuyerBlockerType, value["blocker_type"], "blocker_type"),
            known_decision_factors=_fact_list(value["known_decision_factors"]),
            missing_decision_information=_fact_list(value["missing_decision_information"]),
            recommended_next_step=_enum_value(
                BuyerNextStep, value["recommended_next_step"], "recommended_next_step"
            ),
            outcome=_enum_value(BuyerIndecisionOutcome, value["outcome"], "outcome"),
            escalation_reason=_optional_text(value["escalation_reason"], "escalation_reason", 500),
            language=_enum_value(SalesLanguage, value["language"], "language"),
        )


ConversationExpertiseOutput = NeedsDiscoveryOutput | ObjectionHandlingOutput | BuyerIndecisionOutput


@dataclass(frozen=True, slots=True)
class ConversationExpertiseExecutionResult:
    response_text: str
    outcome: NeedsDiscoveryOutcome | ObjectionOutcome | BuyerIndecisionOutcome
    validation_outcome: ConversationSkillValidationOutcome
    validation_reason: str
    ai_invoked: bool
    structured_result: dict[str, object]
    escalation_kind: str | None = None


def select_sales_conversation_skill(customer_message: str) -> str | None:
    """Apply explicit server-owned priority; customer skill names grant nothing."""

    if is_pricing_explanation_turn(customer_message):
        return "pricing_explanation"
    if _OBJECTION_SIGNAL.search(customer_message):
        return OBJECTION_HANDLING_KEY
    if _INDECISION_SIGNAL.search(customer_message):
        return BUYER_INDECISION_KEY
    if _BUYING_INTENT_SIGNAL.search(customer_message):
        return None
    if _DISCOVERY_SIGNAL.search(customer_message):
        return NEEDS_DISCOVERY_KEY
    return None


def objection_type_for(customer_message: str) -> ObjectionType:
    folded = customer_message.casefold()
    if _GUARANTEE_REQUEST.search(customer_message):
        return ObjectionType.GUARANTEE
    if _INTEGRATION_TERM.search(customer_message):
        return ObjectionType.INTEGRATION
    if any(
        term in folded
        for term in ("expensive", "too much", "trop cher", "trop chère", "غالي", "مكلف")
    ):
        return ObjectionType.PRICE_VALUE
    if any(
        term in folded
        for term in ("already use", "already using", "already have", "déjà", "نستخدم")
    ):
        return ObjectionType.EXISTING_SOLUTION
    if any(
        term in folded for term in ("accuracy", "incorrect", "wrong", "worried", "inquiet", "قلق")
    ):
        return ObjectionType.ACCURACY_RISK
    if any(term in folded for term in ("time", "temps", "وقت")):
        return ObjectionType.IMPLEMENTATION_EFFORT
    if any(
        term in folded
        for term in (
            "won't work",
            "will not work",
            "doesn't work",
            "do not think this will work",
            "don't think this will work",
            "fonctionner",
            "marcher",
            "لن ينجح",
            "لا يعمل",
        )
    ):
        return ObjectionType.BUSINESS_FIT
    return ObjectionType.OTHER


def buyer_blocker_for(customer_message: str) -> BuyerBlockerType:
    folded = customer_message.casefold()
    if any(term in folded for term in ("compare", "options", "comparer", "خيارات", "أقارن")):
        return BuyerBlockerType.COMPARISON
    if any(term in folded for term in ("risk", "worried", "concern", "risque", "قلق", "مخاطر")):
        return BuyerBlockerType.RISK
    if any(
        term in folded
        for term in ("complex", "too many", "compliqué", "trop d'options", "معقد", "كثيرة")
    ):
        return BuyerBlockerType.COMPLEXITY
    if any(term in folded for term in ("later", "timing", "not now", "plus tard", "لاحق", "الوقت")):
        return BuyerBlockerType.TIMING
    return BuyerBlockerType.UNKNOWN


class NeedsDiscoveryOutputValidator:
    def validate(
        self, value: object, source: ConversationExpertiseInput | None = None
    ) -> NeedsDiscoveryOutput:
        if not isinstance(value, NeedsDiscoveryOutput) or source is None:
            raise ConversationExpertiseValidationError("Discovery validator requires typed input")
        _validate_language_and_safety(value.response_text, value.language, source)
        _require_evidence(value.discovered_facts, SalesEvidenceClassification.CONFIRMED, source)
        _require_evidence(value.inferred_needs, SalesEvidenceClassification.INFERENCE, source)
        _require_evidence(value.missing_information, SalesEvidenceClassification.UNKNOWN, source)
        questions = _question_count(value.response_text)
        if questions > 1:
            raise ConversationExpertiseValidationError("Discovery may ask only one question")
        if value.outcome is NeedsDiscoveryOutcome.CONTINUE_DISCOVERY and questions != 1:
            raise ConversationExpertiseValidationError("Continued discovery requires one question")
        if value.outcome is NeedsDiscoveryOutcome.SUFFICIENT_CONTEXT and questions:
            raise ConversationExpertiseValidationError("Sufficient context must not interrogate")
        expected_next_steps = {
            NeedsDiscoveryOutcome.CONTINUE_DISCOVERY: DiscoveryNextStep.ASK_ONE_QUESTION,
            NeedsDiscoveryOutcome.SUFFICIENT_CONTEXT: DiscoveryNextStep.PROCEED,
            NeedsDiscoveryOutcome.HANDOFF_REQUIRED: DiscoveryNextStep.HUMAN_HANDOFF,
        }
        if value.next_step is not expected_next_steps[value.outcome]:
            raise ConversationExpertiseValidationError("Discovery next step is inconsistent")
        if questions and _repeats_prior_question(value.response_text, source):
            raise ConversationExpertiseValidationError("Discovery repeats a known question")
        if questions and _asks_for_known_information(value.response_text, source):
            raise ConversationExpertiseValidationError("Discovery asks for known information")
        return value


class ObjectionHandlingOutputValidator:
    def validate(
        self, value: object, source: ConversationExpertiseInput | None = None
    ) -> ObjectionHandlingOutput:
        if not isinstance(value, ObjectionHandlingOutput) or source is None:
            raise ConversationExpertiseValidationError("Objection validator requires typed input")
        _validate_language_and_safety(value.response_text, value.language, source)
        _require_evidence(value.evidence_used, SalesEvidenceClassification.CONFIRMED, source)
        if _question_count(value.response_text) > 1:
            raise ConversationExpertiseValidationError("Objection response asks too many questions")
        expected = objection_type_for(source.customer_message)
        if expected is not ObjectionType.OTHER and value.objection_type is not expected:
            raise ConversationExpertiseValidationError(
                "Objection type does not match the customer concern"
            )
        if not _objection_response_matches(value.response_text, expected):
            raise ConversationExpertiseValidationError(
                "Response does not address the actual objection"
            )
        escalating = value.outcome in {
            ObjectionOutcome.COMMERCIAL_ESCALATION,
            ObjectionOutcome.HUMAN_HANDOFF,
        }
        if escalating != bool(value.escalation_reason):
            raise ConversationExpertiseValidationError("Objection escalation structure is invalid")
        if escalating and value.next_step is not ObjectionNextStep.HUMAN_REVIEW:
            raise ConversationExpertiseValidationError("Objection next step is inconsistent")
        if (
            value.outcome is ObjectionOutcome.NEEDS_CLARIFICATION
            and value.next_step is not ObjectionNextStep.CLARIFY_CONCERN
        ):
            raise ConversationExpertiseValidationError("Objection next step is inconsistent")
        if expected is ObjectionType.GUARANTEE and not escalating:
            raise ConversationExpertiseValidationError("Guarantee request requires human review")
        return value


class BuyerIndecisionOutputValidator:
    def validate(
        self, value: object, source: ConversationExpertiseInput | None = None
    ) -> BuyerIndecisionOutput:
        if not isinstance(value, BuyerIndecisionOutput) or source is None:
            raise ConversationExpertiseValidationError("Indecision validator requires typed input")
        _validate_language_and_safety(value.response_text, value.language, source)
        _require_evidence(
            value.known_decision_factors,
            SalesEvidenceClassification.CONFIRMED,
            source,
        )
        _require_evidence(
            value.missing_decision_information,
            SalesEvidenceClassification.UNKNOWN,
            source,
        )
        if _question_count(value.response_text) > 1:
            raise ConversationExpertiseValidationError(
                "Indecision response asks too many questions"
            )
        if _PRESSURE_REPLY.search(value.response_text):
            raise ConversationExpertiseValidationError("Indecision response applies pressure")
        if _AUTOMATIC_FOLLOW_UP_REPLY.search(value.response_text):
            raise ConversationExpertiseValidationError("Skill cannot execute a follow-up")
        escalating = value.outcome is BuyerIndecisionOutcome.HUMAN_HANDOFF
        if escalating != bool(value.escalation_reason):
            raise ConversationExpertiseValidationError("Indecision escalation structure is invalid")
        allowed_next_steps = {
            BuyerIndecisionOutcome.SUPPORTED: {
                BuyerNextStep.SUMMARIZE_FACTORS,
                BuyerNextStep.ANSWER_QUESTION,
                BuyerNextStep.WAIT,
            },
            BuyerIndecisionOutcome.NEEDS_CLARIFICATION: {
                BuyerNextStep.CLARIFY_CONCERN,
                BuyerNextStep.ANSWER_QUESTION,
            },
            BuyerIndecisionOutcome.WAIT_RECOMMENDED: {
                BuyerNextStep.WAIT,
                BuyerNextStep.FOLLOW_UP_LATER,
            },
            BuyerIndecisionOutcome.HUMAN_HANDOFF: {BuyerNextStep.HUMAN_DISCUSSION},
        }
        if value.recommended_next_step not in allowed_next_steps[value.outcome]:
            raise ConversationExpertiseValidationError("Indecision next step is inconsistent")
        expected = buyer_blocker_for(source.customer_message)
        if expected is not BuyerBlockerType.UNKNOWN and value.blocker_type is not expected:
            raise ConversationExpertiseValidationError("Buyer blocker does not match the message")
        return value


def conversation_expertise_components(
    definition: AgentSkillDefinition,
) -> ResolvedAgentSkillComponents:
    key = definition.key
    contracts: dict[str, type[object]] = {
        NEEDS_DISCOVERY_KEY: NeedsDiscoveryOutput,
        OBJECTION_HANDLING_KEY: ObjectionHandlingOutput,
        BUYER_INDECISION_KEY: BuyerIndecisionOutput,
    }
    validators = {
        NEEDS_DISCOVERY_KEY: NeedsDiscoveryOutputValidator(),
        OBJECTION_HANDLING_KEY: ObjectionHandlingOutputValidator(),
        BUYER_INDECISION_KEY: BuyerIndecisionOutputValidator(),
    }
    if key not in _SUPPORTED_KEYS:
        raise ConversationExpertiseContractError("Unsupported conversation expertise skill")
    resolver = AgentSkillComponentResolver(
        AgentSkillContractRegistry(
            (
                (definition.input_contract, ConversationExpertiseInput),
                (definition.output_contract, contracts[key]),
            )
        ),
        AgentSkillValidatorRegistry(((definition.validator, validators[key]),)),
    )
    return resolver.resolve(definition)


def parse_conversation_expertise_output(skill_key: str, raw: str) -> ConversationExpertiseOutput:
    if skill_key == NEEDS_DISCOVERY_KEY:
        return NeedsDiscoveryOutput.from_json(raw)
    if skill_key == OBJECTION_HANDLING_KEY:
        return ObjectionHandlingOutput.from_json(raw)
    if skill_key == BUYER_INDECISION_KEY:
        return BuyerIndecisionOutput.from_json(raw)
    raise ConversationExpertiseContractError("Unsupported conversation expertise skill")


def skill_instructions(skill_key: str) -> str:
    instructions = {
        NEEDS_DISCOVERY_KEY: NEEDS_DISCOVERY_INSTRUCTIONS,
        OBJECTION_HANDLING_KEY: OBJECTION_HANDLING_INSTRUCTIONS,
        BUYER_INDECISION_KEY: BUYER_INDECISION_INSTRUCTIONS,
    }
    try:
        return instructions[skill_key]
    except KeyError as exc:
        raise ConversationExpertiseContractError(
            "Unsupported conversation expertise skill"
        ) from exc


def safe_conversation_expertise_result(
    skill_key: str,
    source: ConversationExpertiseInput,
    *,
    validation_rejected: bool = False,
) -> ConversationExpertiseExecutionResult:
    if skill_key == NEEDS_DISCOVERY_KEY:
        output = _safe_discovery(source)
        escalation = None
    elif skill_key == OBJECTION_HANDLING_KEY:
        output, escalation = _safe_objection(source, validation_rejected)
    elif skill_key == BUYER_INDECISION_KEY:
        output = _safe_indecision(source)
        escalation = None
    else:
        raise ConversationExpertiseContractError("Unsupported conversation expertise skill")
    return ConversationExpertiseExecutionResult(
        response_text=output.response_text,
        outcome=output.outcome,
        validation_outcome=(
            ConversationSkillValidationOutcome.REJECTED
            if validation_rejected
            else ConversationSkillValidationOutcome.ACCEPTED
        ),
        validation_reason=(
            "generated_output_rejected" if validation_rejected else "safe_deterministic_result"
        ),
        ai_invoked=False,
        structured_result=_structured_result(output),
        escalation_kind=escalation,
    )


def accepted_conversation_expertise_result(
    output: ConversationExpertiseOutput,
) -> ConversationExpertiseExecutionResult:
    """Convert validated typed output into the provider-neutral execution result."""

    escalation = None
    if isinstance(output, ObjectionHandlingOutput) and output.outcome in {
        ObjectionOutcome.COMMERCIAL_ESCALATION,
        ObjectionOutcome.HUMAN_HANDOFF,
    }:
        escalation = (
            "unsupported_commitment"
            if output.objection_type is ObjectionType.GUARANTEE
            else "authoritative_information_unavailable"
        )
    elif (
        isinstance(output, BuyerIndecisionOutput)
        and output.outcome is BuyerIndecisionOutcome.HUMAN_HANDOFF
        or isinstance(output, NeedsDiscoveryOutput)
        and output.outcome is NeedsDiscoveryOutcome.HANDOFF_REQUIRED
    ):
        escalation = "authoritative_information_unavailable"
    return ConversationExpertiseExecutionResult(
        response_text=output.response_text,
        outcome=output.outcome,
        validation_outcome=ConversationSkillValidationOutcome.ACCEPTED,
        validation_reason="grounded_output_accepted",
        ai_invoked=True,
        structured_result=_structured_result(output),
        escalation_kind=escalation,
    )


def _safe_discovery(source: ConversationExpertiseInput) -> NeedsDiscoveryOutput:
    corpus = _source_corpus(source).casefold()
    prior_agent_text = " ".join(
        message.content.casefold()
        for message in source.conversation_context
        if message.direction in {"outbound", "human_outbound"}
    )
    has_volume = bool(re.search(r"\b\d+\b.{0,30}\b(?:messages?|leads?|conversations?)\b", corpus))
    has_channel = bool(source.communication_channel)
    has_problem = bool(
        re.search(
            r"(?i)(?:can['’]?t|cannot|struggle|problem|challenge|لا\s+نستطيع|مشكلة|مانجموش)",
            corpus,
        )
    )
    asked_volume = bool(
        re.search(
            r"(?i)(?:how\s+many|combien|9adeh|قداش).{0,35}(?:messages?|conversations?|رسالة|محادثة)",
            prior_agent_text,
        )
    )
    asked_channel = bool(
        re.search(
            r"(?i)(?:(?:which|what)\s+channels?|quels?\s+canaux|أي\s+(?:قناة|قنوات))",
            prior_agent_text,
        )
    )
    asked_goal = bool(
        re.search(
            r"(?i)(?:what\s+(?:outcome|goal)|quel\s+r[ée]sultat|شنوة\s+أكثر\s+نتيجة|chnouwa\s+akther\s+resultat)",
            prior_agent_text,
        )
    )
    if has_volume and has_channel and has_problem:
        text = _localized(source, "discovery_sufficient")
        missing: tuple[SalesEvidenceFact, ...] = ()
        next_step = DiscoveryNextStep.PROCEED
        outcome = NeedsDiscoveryOutcome.SUFFICIENT_CONTEXT
    elif not has_volume and not asked_volume:
        text = _localized(source, "discovery_volume")
        missing = (
            SalesEvidenceFact("monthly conversation volume", SalesEvidenceClassification.UNKNOWN),
        )
        next_step = DiscoveryNextStep.ASK_ONE_QUESTION
        outcome = NeedsDiscoveryOutcome.CONTINUE_DISCOVERY
    elif not has_channel and not asked_channel:
        text = _localized(source, "discovery_channel")
        missing = (SalesEvidenceFact("customer channels", SalesEvidenceClassification.UNKNOWN),)
        next_step = DiscoveryNextStep.ASK_ONE_QUESTION
        outcome = NeedsDiscoveryOutcome.CONTINUE_DISCOVERY
    elif not asked_goal:
        text = _localized(source, "discovery_goal")
        missing = (SalesEvidenceFact("desired outcome", SalesEvidenceClassification.UNKNOWN),)
        next_step = DiscoveryNextStep.ASK_ONE_QUESTION
        outcome = NeedsDiscoveryOutcome.CONTINUE_DISCOVERY
    else:
        text = _localized(source, "discovery_pause")
        missing = ()
        next_step = DiscoveryNextStep.PROCEED
        outcome = NeedsDiscoveryOutcome.SUFFICIENT_CONTEXT
    return NeedsDiscoveryOutput(text, (), (), missing, next_step, outcome, source.language)


def _safe_objection(
    source: ConversationExpertiseInput,
    validation_rejected: bool,
) -> tuple[ObjectionHandlingOutput, str | None]:
    objection_type = objection_type_for(source.customer_message)
    unsupported_integration = (
        objection_type is ObjectionType.INTEGRATION and not _integration_is_grounded(source)
    )
    if objection_type is ObjectionType.GUARANTEE:
        text = _localized(source, "objection_commitment")
        outcome = ObjectionOutcome.HUMAN_HANDOFF
        next_step = ObjectionNextStep.HUMAN_REVIEW
        reason = "unsupported_commitment"
    elif unsupported_integration or validation_rejected:
        text = _localized(source, "objection_unknown")
        outcome = ObjectionOutcome.HUMAN_HANDOFF
        next_step = ObjectionNextStep.HUMAN_REVIEW
        reason = "authoritative_information_unavailable"
    else:
        text = _localized(source, "objection_clarify")
        outcome = ObjectionOutcome.NEEDS_CLARIFICATION
        next_step = ObjectionNextStep.CLARIFY_CONCERN
        reason = None
    return (
        ObjectionHandlingOutput(
            text,
            objection_type,
            (),
            (),
            next_step,
            reason,
            outcome,
            source.language,
        ),
        reason,
    )


def _safe_indecision(source: ConversationExpertiseInput) -> BuyerIndecisionOutput:
    blocker = buyer_blocker_for(source.customer_message)
    if blocker is BuyerBlockerType.TIMING:
        text = _localized(source, "indecision_wait")
        next_step = BuyerNextStep.FOLLOW_UP_LATER
        outcome = BuyerIndecisionOutcome.WAIT_RECOMMENDED
    else:
        text = _localized(source, "indecision_clarify")
        next_step = BuyerNextStep.CLARIFY_CONCERN
        outcome = BuyerIndecisionOutcome.NEEDS_CLARIFICATION
    return BuyerIndecisionOutput(text, blocker, (), (), next_step, outcome, None, source.language)


def _localized(source: ConversationExpertiseInput, kind: str) -> str:
    messages = {
        SalesLanguage.ENGLISH: {
            "discovery_volume": "About how many customer conversations do you handle in a typical month?",
            "discovery_goal": "What outcome would make the biggest difference for your team right now?",
            "discovery_channel": "Which customer channels matter most for this workflow?",
            "discovery_sufficient": "Thanks, that gives me enough context to focus on the most relevant next step.",
            "discovery_pause": "Thanks, I’ll use the context you already shared and avoid repeating questions.",
            "objection_clarify": "That concern makes sense. Which part matters most for your decision?",
            "objection_commitment": "I can't make that guarantee. A team member needs to review the commitment with you.",
            "objection_unknown": "I understand the concern, but I can't confirm that from the verified information available. A team member should review it with you.",
            "indecision_clarify": "There is no need to rush. What is the main point you still need to feel clear about?",
            "indecision_wait": "That is completely reasonable. You can take the time you need, and a later follow-up can be considered if useful.",
        },
        SalesLanguage.FRENCH: {
            "discovery_volume": "Environ combien de conversations clients gérez-vous habituellement par mois ?",
            "discovery_goal": "Quel résultat ferait la plus grande différence pour votre équipe aujourd’hui ?",
            "discovery_channel": "Quels canaux clients sont les plus importants pour ce processus ?",
            "discovery_sufficient": "Merci, j’ai assez de contexte pour me concentrer sur la prochaine étape la plus pertinente.",
            "discovery_pause": "Merci, je vais utiliser le contexte déjà partagé sans répéter les questions.",
            "objection_clarify": "Cette préoccupation est légitime. Quel aspect compte le plus dans votre décision ?",
            "objection_commitment": "Je ne peux pas donner cette garantie. Un membre de l’équipe doit examiner cet engagement avec vous.",
            "objection_unknown": "Je comprends la préoccupation, mais je ne peux pas la confirmer avec les informations vérifiées disponibles. Un membre de l’équipe doit l’examiner avec vous.",
            "indecision_clarify": "Il n’est pas nécessaire de vous presser. Quel est le principal point que vous souhaitez encore clarifier ?",
            "indecision_wait": "C’est tout à fait raisonnable. Prenez le temps nécessaire ; un suivi ultérieur pourra être envisagé si utile.",
        },
        SalesLanguage.ARABIC: {
            "discovery_volume": "كم محادثة مع العملاء تديرون عادةً خلال الشهر؟",
            "discovery_goal": "ما النتيجة التي ستحدث أكبر فرق لفريقكم الآن؟",
            "discovery_channel": "ما قنوات العملاء الأكثر أهمية لهذا المسار؟",
            "discovery_sufficient": "شكرًا، لدي الآن سياق كافٍ للتركيز على الخطوة التالية الأنسب.",
            "discovery_pause": "شكرًا، سأستخدم السياق الذي شاركتموه بالفعل من دون تكرار الأسئلة.",
            "objection_clarify": "هذا القلق مفهوم. ما الجانب الأكثر أهمية في قراركم؟",
            "objection_commitment": "لا أستطيع تقديم هذا الضمان. يجب أن يراجع أحد أعضاء الفريق هذا الالتزام معكم.",
            "objection_unknown": "أتفهم هذا القلق، لكن لا أستطيع تأكيده بالمعلومات الموثقة المتاحة. يجب أن يراجعه أحد أعضاء الفريق معكم.",
            "indecision_clarify": "لا حاجة إلى التسرع. ما النقطة الأساسية التي ما زلتم بحاجة إلى توضيحها؟",
            "indecision_wait": "هذا أمر مفهوم تمامًا. يمكنكم أخذ الوقت اللازم، ويمكن التفكير في متابعة لاحقة إذا كانت مفيدة.",
        },
        SalesLanguage.TUNISIAN_ARABIC: {
            "discovery_volume": "9adeh men conversation m3a les clients ta3mlou ta9riban fi chhar?",
            "discovery_goal": "Chnouwa akther resultat ynajem yaamel far9 m3a l'equipe mte3kom tawa?",
            "discovery_channel": "Chnouwa les canaux clients elli yhemmoukom akther fel workflow hedha?",
            "discovery_sufficient": "Merci, tawa 3andi contexte kefi bech nrakez 3al prochaine etape el ansab.",
            "discovery_pause": "Merci, bech nesta3mel el contexte elli 3titouh w man3awedch nafs les questions.",
            "objection_clarify": "El concern hedha mafhoum. Chnouwa akther point yhemmkom fel decision?",
            "objection_commitment": "Manajamch na3ti el garantie hedhi. Yelzem wehed mel equipe yraja3 el engagement m3akom.",
            "objection_unknown": "Nefhem el concern, ama manajamch n2akked el ma3louma bel donnees confirmees. Yelzem wehed mel equipe yraja3ha m3akom.",
            "indecision_clarify": "Ma fama hata este3jel. Chnouwa aham point mezel yelzem yetwadha7?",
            "indecision_wait": "Hedha 3adi. Khoudhou el wa9t elli yelzemkom, w follow-up ba3d ynajem yet9tara7 ken yfidkom.",
        },
    }
    text = messages[source.language][kind]
    if source.language is SalesLanguage.TUNISIAN_ARABIC:
        arabic = {
            "discovery_volume": "قداش من محادثة مع الحرفاء تتعاملوا معاها تقريبًا في الشهر؟",
            "discovery_goal": "شنوة أكثر نتيجة تنجم تعمل فرق لفريقكم توا؟",
            "discovery_channel": "شنوة قنوات الحرفاء اللي تهمكم أكثر في المسار هذا؟",
            "discovery_sufficient": "يعطيكم الصحة، توا عندي سياق كافي باش نركز على أنسب خطوة جاية.",
            "discovery_pause": "يعطيكم الصحة، باش نستعمل السياق اللي عطيتوه وما نعاودش نفس الأسئلة.",
            "objection_clarify": "القلق هذا مفهوم. شنوة أكثر نقطة تهمكم في القرار؟",
            "objection_commitment": "مانجمش نعطي الضمان هذا. يلزم واحد من الفريق يراجع الالتزام معاكم.",
            "objection_unknown": "نفهم القلق، أما مانجمش نأكد المعلومة بالمعطيات الموثقة الموجودة. يلزم واحد من الفريق يراجعها معاكم.",
            "indecision_clarify": "ما فما حتى استعجال. شنوة أهم نقطة مازالت يلزمها توضيح؟",
            "indecision_wait": "هذا عادي. خذوا الوقت اللي يلزمكم، وتنجموا تفكروا في متابعة بعد إذا تفيدكم.",
        }
        if source.script is SalesWritingScript.ARABIC:
            text = arabic[kind]
    return text


def _validate_language_and_safety(
    response_text: str,
    language: SalesLanguage,
    source: ConversationExpertiseInput,
) -> None:
    if language is not source.language:
        raise ConversationExpertiseValidationError("Response language is not authorized")
    detected = detect_sales_language(response_text)
    allowed = {source.language}
    if (
        source.language is SalesLanguage.TUNISIAN_ARABIC
        and source.script is SalesWritingScript.ARABIC
    ):
        allowed.add(SalesLanguage.ARABIC)
    if detected not in allowed:
        raise ConversationExpertiseValidationError(
            "Response text does not use the authorized language"
        )
    script = validate_sales_script_consistency(
        text=response_text,
        style=SalesCommunicationStyle(language=source.language, script=source.script),
    )
    if not script.is_consistent:
        raise ConversationExpertiseValidationError("Response script is not authorized")
    if _UNSAFE_COMMERCIAL_REPLY.search(response_text):
        raise ConversationExpertiseValidationError(
            "Response contains an unsupported commercial claim"
        )
    if _INVENTED_CUSTOMER_ASSERTION.search(response_text):
        raise ConversationExpertiseValidationError("Response invents a customer need")
    if _ASSERTED_PRODUCT_CLAIM.search(response_text) and not _claim_is_grounded(
        response_text, source
    ):
        raise ConversationExpertiseValidationError("Product claim is not authoritative")
    authoritative_numbers = set(_NUMBER.findall(_source_corpus(source)))
    if not set(_NUMBER.findall(response_text)).issubset(authoritative_numbers):
        raise ConversationExpertiseValidationError("Response contains an unsupported number")


def _require_evidence(
    facts: tuple[SalesEvidenceFact, ...],
    expected: SalesEvidenceClassification,
    source: ConversationExpertiseInput,
) -> None:
    corpus = _source_corpus(source).casefold()
    for item in facts:
        if item.evidence is not expected:
            raise ConversationExpertiseValidationError("Evidence boundary is invalid")
        if expected is SalesEvidenceClassification.CONFIRMED and item.fact.casefold() not in corpus:
            raise ConversationExpertiseValidationError("Confirmed evidence is not authoritative")


def _source_corpus(source: ConversationExpertiseInput) -> str:
    parts = [source.customer_message]
    parts.extend(message.content for message in source.conversation_context)
    parts.extend(fact.fact for fact in source.lead_facts)
    for product in source.products:
        parts.extend((product.name, product.description))
        if product.price is not None:
            parts.append(f"{product.price:.2f}")
        if product.billing_period:
            parts.append(product.billing_period)
    return "\n".join(parts)


def _claim_is_grounded(text: str, source: ConversationExpertiseInput) -> bool:
    catalog = " ".join(
        f"{product.name} {product.description}" for product in source.products
    ).casefold()
    terms = {str(term).casefold() for term in _INTEGRATION_TERM.findall(text)}
    return bool(terms) and all(term in catalog for term in terms)


def _integration_is_grounded(source: ConversationExpertiseInput) -> bool:
    requested = {
        str(term).casefold() for term in _INTEGRATION_TERM.findall(source.customer_message)
    }
    catalog = " ".join(
        f"{product.name} {product.description}" for product in source.products
    ).casefold()
    return bool(requested) and all(term in catalog for term in requested)


def _repeats_prior_question(text: str, source: ConversationExpertiseInput) -> bool:
    current = _normalized_question(text)
    if not current:
        return False
    for message in source.conversation_context:
        if message.direction in {"outbound", "human_outbound"} and (
            "?" in message.content or "؟" in message.content
        ):
            prior = _normalized_question(message.content)
            prior_tokens = set(prior.split())
            current_tokens = set(current.split())
            overlap = len(prior_tokens.intersection(current_tokens))
            if (
                prior == current
                or prior in current
                or current in prior
                or (
                    min(len(prior_tokens), len(current_tokens)) >= 4
                    and overlap / min(len(prior_tokens), len(current_tokens)) >= 0.8
                )
            ):
                return True
    return False


def _asks_for_known_information(
    response_text: str,
    source: ConversationExpertiseInput,
) -> bool:
    response = response_text.casefold()
    corpus = _source_corpus(source).casefold()
    asks_channel = re.search(
        r"(?i)(?:which|what)\s+(?:communication\s+)?channels?|"
        r"quels?\s+canaux|أي\s+(?:قناة|قنوات)",
        response,
    )
    if source.communication_channel and asks_channel:
        return True
    known_dimensions = (
        (
            re.compile(r"\b\d+\b.{0,25}\b(?:employees?|people|staff|employ[ée]s?)\b"),
            re.compile(
                r"(?i)(?:company|team)\s+size|how\s+many\s+employees|taille\s+(?:de\s+)?(?:l['’]entreprise|l['’][ée]quipe)|عدد\s+(?:الموظفين|الفريق)"
            ),
        ),
        (
            re.compile(r"\b\d+\b.{0,30}\b(?:messages?|leads?|conversations?)\b"),
            re.compile(
                r"(?i)how\s+many\s+(?:messages?|leads?|conversations?)|combien\s+de\s+(?:messages?|prospects?|conversations?)|كم\s+(?:رسالة|محادثة)"
            ),
        ),
    )
    return any(known.search(corpus) and asked.search(response) for known, asked in known_dimensions)


def _objection_response_matches(text: str, objection_type: ObjectionType) -> bool:
    patterns = {
        ObjectionType.PRICE_VALUE: re.compile(
            r"(?i)(?:price|cost|expensive|value|prix|tarif|cher|co[uû]t|سعر|قيمة|غالي|مكلف)"
        ),
        ObjectionType.EXISTING_SOLUTION: re.compile(
            r"(?i)(?:existing|current|solution|tool|already\s+use|solution\s+actuelle|outil|déjà|حل|أداة|تستخدم)"
        ),
        ObjectionType.ACCURACY_RISK: re.compile(
            r"(?i)(?:accuracy|correct|incorrect|error|concern|risk|fiabilit[ée]|erreur|pr[ée]occupation|دقة|خطأ|قلق|مخاطر)"
        ),
        ObjectionType.IMPLEMENTATION_EFFORT: re.compile(
            r"(?i)(?:time|implementation|setup|effort|temps|mise\s+en\s+place|وقت|تنفيذ|إعداد)"
        ),
        ObjectionType.BUSINESS_FIT: re.compile(
            r"(?i)(?:fit|work|suitable|business|adapt[ée]|fonctionn|march|يناسب|يعمل|نشاط)"
        ),
        ObjectionType.INTEGRATION: _INTEGRATION_TERM,
        ObjectionType.GUARANTEE: re.compile(
            r"(?i)(?:guarantee|commitment|human|review|garantie|engagement|équipe|ضمان|التزام|الفريق)"
        ),
        ObjectionType.OTHER: re.compile(
            r"(?i)(?:concern|question|point|pr[ée]occupation|question|point|قلق|نقطة)"
        ),
    }
    return patterns[objection_type].search(text) is not None


def _normalized_question(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def _question_count(text: str) -> int:
    return text.count("?") + text.count("؟")


def _structured_result(output: ConversationExpertiseOutput) -> dict[str, object]:
    value = asdict(output)
    value.pop("response_text", None)
    value.pop("language", None)
    return _enum_safe(value)


def _enum_safe(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _enum_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_safe(item) for item in value]
    return value


def _json_object(raw: str) -> dict[str, object]:
    normalized = raw.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*|\s*```$", "", normalized, flags=re.IGNORECASE)
    try:
        value = json.loads(normalized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConversationExpertiseContractError("Skill output must be one JSON object") from exc
    if not isinstance(value, dict):
        raise ConversationExpertiseContractError("Skill output must be an object")
    return value


def _require_exact_fields(value: dict[str, object], required: set[str]) -> None:
    if set(value) != required:
        raise ConversationExpertiseContractError("Skill output fields are invalid")


def _fact_list(value: object) -> tuple[SalesEvidenceFact, ...]:
    if not isinstance(value, list) or len(value) > 20:
        raise ConversationExpertiseContractError("Evidence facts must be a bounded list")
    return tuple(SalesEvidenceFact.from_value(item) for item in value)


def _text_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 20:
        raise ConversationExpertiseContractError(f"{field} must be a bounded list")
    return tuple(_required_text(item, field, 500) for item in value)


def _required_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ConversationExpertiseContractError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ConversationExpertiseContractError(f"{field} is invalid")
    return normalized


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, field, maximum)


def _enum_value(enum_type: type[StrEnum], value: object, field: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ConversationExpertiseContractError(f"{field} is invalid") from exc
