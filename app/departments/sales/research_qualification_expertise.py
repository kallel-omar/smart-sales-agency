"""Governed research and qualification AgentSkills for Task 296F.

The module is intentionally tool-free. It operates only on bounded HIRI context,
validates typed evidence, and never performs external research or changes the
existing deterministic qualification score.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.core.agent_skill_execution import (
    AgentSkillComponentResolver,
    AgentSkillContractRegistry,
    AgentSkillValidatorRegistry,
    ResolvedAgentSkillComponents,
)
from app.core.agent_skills import AgentSkillDefinition
from app.departments.sales.evidence import (
    SalesEvidenceClassification,
    SalesEvidenceContractError,
    SalesEvidenceItem,
    SalesEvidenceSourceType,
)
from app.models import ConversationMessage, Lead

ACCOUNT_RESEARCH_KEY = "account_research"
BUYING_SIGNAL_DETECTION_KEY = "buying_signal_detection"
QUALIFICATION_GAP_DETECTOR_KEY = "qualification_gap_detector"
RESEARCH_QUALIFICATION_VERSION = "v1"

ACCOUNT_RESEARCH_INSTRUCTIONS = (
    "Account research skill v1: Use only the authoritative HIRI evidence sources "
    "provided in the task. Do not browse or claim external research. Do not invent "
    "industry, employee count, revenue, funding, technology stack, customer volume, "
    "integrations, or business problems. Keep confirmed facts exact, label reasoned "
    "interpretations as inference, and keep unavailable facts unknown. Return one JSON "
    "object only with keys company_summary, confirmed_facts, inferred_context, unknowns, "
    "potential_needs, research_gaps, and outcome. Evidence items must contain exactly "
    "classification, claim, source_type, source_reference, and captured_at. outcome must "
    "be context_researched, limited_context, or human_review."
)

_UNSUPPORTED_COMPANY_FACT = re.compile(
    r"(?i)(?:\b\d+[\s-]*(?:employees?|staff|people)\b|"
    r"\b(?:revenue|turnover|funding|funded|valuation|industry|sector|"
    r"technology\s+stack|tech\s+stack)\b|"
    r"\b(?:healthcare|fintech|retail|manufacturing|saas)\s+(?:company|business|provider)\b)"
)
_NUMBER = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])")
_PRICING_SIGNAL = re.compile(
    r"(?i)(?:\bprice|pricing|cost|budget|quote|prix|tarif|co[uû]t|سعر|ميزانية)"
)
_IMPLEMENTATION_SIGNAL = re.compile(
    r"(?i)(?:\bimplement|implementation|setup|onboard|deploy|go\s+live|"
    r"mise\s+en\s+place|d[ée]ployer|تنفيذ|إعداد)"
)
_INTEGRATION_SIGNAL = re.compile(
    r"(?i)(?:\bintegration|integrate|connect|works?\s+with|int[ée]gration|connecter|تكامل|ربط)"
)
_TIMELINE_SOON = re.compile(
    r"(?i)(?:\bthis\s+(?:week|month|quarter)|\bnext\s+(?:week|month)|\basap\b|"
    r"\bsoon\b|\bby\s+\w+|cette\s+semaine|ce\s+mois|rapidement|قريبًا|هذا\s+الشهر)"
)
_TIMELINE_LATER = re.compile(
    r"(?i)(?:\bnot\s+this\s+(?:month|quarter)|\bnext\s+year|\blater\b|"
    r"pas\s+maintenant|plus\s+tard|l['’]ann[ée]e\s+prochaine|لاحقًا|العام\s+القادم)"
)
_AUTHORITY_SIGNAL = re.compile(
    r"(?i)(?:\bi\s+(?:decide|approve|sign)|\bdecision[ -]?maker|\bmy\s+(?:manager|director)\b|"
    r"je\s+(?:d[ée]cide|valide|signe)|d[ée]cisionnaire|أنا\s+(?:أقرر|أوافق)|صاحب\s+القرار)"
)
_DEMO_SIGNAL = re.compile(
    r"(?i)(?:\bdemo\b|\bbook\s+(?:a\s+)?call\b|\bcontact\s+me\b|"
    r"\btalk\s+to\s+sales\b|d[ée]monstration|appelez[- ]moi|عرض\s+توضيحي|اتصلوا\s+بي)"
)
_BUSINESS_PAIN_SIGNAL = re.compile(
    r"(?i)(?:\bwe\s+struggle|\bwe\s+can['’]?t|\bproblem\b|\bchallenge\b|"
    r"\btoo\s+many\s+(?:messages?|leads?)|nous\s+n['’]arrivons\s+pas|probl[eè]me|"
    r"لا\s+نستطيع|مشكلة|رسائل\s+كثيرة)"
)
_PURCHASE_INTENT_SIGNAL = re.compile(
    r"(?i)(?:\bready\s+to\s+(?:buy|start|sign)|\bwant\s+to\s+(?:buy|start|sign)|"
    r"\bsend\s+(?:the\s+)?contract|\bsign\s+up\b|pr[êe]t\s+[àa]\s+(?:acheter|commencer)|"
    r"envoyez\s+le\s+contrat|جاهز\s+(?:للشراء|للبدء)|أرسلوا\s+العقد)"
)
_DISQUALIFICATION_SIGNAL = re.compile(
    r"(?i)(?:\bnot\s+interested\b|\bdo\s+not\s+contact\b|\bnot\s+a\s+fit\b|"
    r"pas\s+int[ée]ress[ée]|ne\s+me\s+contactez\s+pas|لا\s+أرغب|لا\s+تتصلوا\s+بي)"
)


class AccountResearchOutcome(StrEnum):
    CONTEXT_RESEARCHED = "context_researched"
    LIMITED_CONTEXT = "limited_context"
    HUMAN_REVIEW = "human_review"


class BuyingSignalType(StrEnum):
    PRICING_INTEREST = "pricing_interest"
    IMPLEMENTATION_QUESTION = "implementation_question"
    INTEGRATION_QUESTION = "integration_question"
    TIMELINE_STATEMENT = "timeline_statement"
    DECISION_MAKER_INVOLVEMENT = "decision_maker_involvement"
    DEMO_OR_CONTACT_REQUEST = "demo_or_contact_request"
    REPEATED_ENGAGEMENT = "repeated_engagement"
    EXPLICIT_BUSINESS_PAIN = "explicit_business_pain"
    PURCHASE_INTENT = "purchase_intent"


class BuyingSignalStrength(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BuyingSignalOutcome(StrEnum):
    SIGNALS_DETECTED = "signals_detected"
    NO_SUPPORTED_SIGNALS = "no_supported_signals"


class QualificationGapOutcome(StrEnum):
    SUFFICIENT_FOR_CURRENT_STAGE = "sufficient_for_current_stage"
    MORE_INFORMATION_NEEDED = "more_information_needed"
    LIKELY_UNQUALIFIED = "likely_unqualified"
    HUMAN_REVIEW = "human_review"


class ExpertiseValidationOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ResearchQualificationContractError(ValueError):
    """Raised when a generated or constructed expertise contract is malformed."""


class ResearchQualificationValidationError(ValueError):
    """Raised when expertise output exceeds its authoritative evidence."""


@dataclass(frozen=True, slots=True)
class AuthoritativeSalesEvidence:
    source_reference: str
    source_type: SalesEvidenceSourceType
    claim: str
    captured_at: str | None = None

    def render(self) -> str:
        captured = self.captured_at or "unavailable"
        return (
            f"Reference: {self.source_reference}\n"
            f"Source type: {self.source_type.value}\n"
            f"Captured at: {captured}\n"
            f"Claim: {self.claim}"
        )


@dataclass(frozen=True, slots=True)
class AccountResearchInput:
    workspace_id: UUID
    lead_id: UUID
    sources: tuple[AuthoritativeSalesEvidence, ...]
    workspace_instructions: str | None = None

    def render(self) -> str:
        return "\n\n".join(source.render() for source in self.sources)


@dataclass(frozen=True, slots=True)
class AccountResearchOutput:
    company_summary: str
    confirmed_facts: tuple[SalesEvidenceItem, ...]
    inferred_context: tuple[SalesEvidenceItem, ...]
    unknowns: tuple[SalesEvidenceItem, ...]
    potential_needs: tuple[SalesEvidenceItem, ...]
    research_gaps: tuple[str, ...]
    outcome: AccountResearchOutcome

    @classmethod
    def from_json(cls, raw: str) -> AccountResearchOutput:
        value = _json_object(raw)
        _require_fields(
            value,
            {
                "company_summary",
                "confirmed_facts",
                "inferred_context",
                "unknowns",
                "potential_needs",
                "research_gaps",
                "outcome",
            },
        )
        return cls(
            company_summary=_required_text(value["company_summary"], "company_summary", 4_000),
            confirmed_facts=_evidence_list(value["confirmed_facts"]),
            inferred_context=_evidence_list(value["inferred_context"]),
            unknowns=_evidence_list(value["unknowns"]),
            potential_needs=_evidence_list(value["potential_needs"]),
            research_gaps=_text_list(value["research_gaps"], "research_gaps", 20),
            outcome=_enum(AccountResearchOutcome, value["outcome"], "outcome"),
        )


@dataclass(frozen=True, slots=True)
class BuyingSignal:
    signal_type: BuyingSignalType
    strength: BuyingSignalStrength
    supporting_evidence: tuple[str, ...]

    @classmethod
    def from_value(cls, value: object) -> BuyingSignal:
        if not isinstance(value, dict):
            raise ResearchQualificationContractError("Buying signal must be an object")
        _require_fields(value, {"signal_type", "strength", "supporting_evidence"})
        return cls(
            signal_type=_enum(BuyingSignalType, value["signal_type"], "signal_type"),
            strength=_enum(BuyingSignalStrength, value["strength"], "strength"),
            supporting_evidence=_text_list(
                value["supporting_evidence"], "supporting_evidence", 10
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "signal_type": self.signal_type.value,
            "strength": self.strength.value,
            "supporting_evidence": list(self.supporting_evidence),
        }


@dataclass(frozen=True, slots=True)
class BuyingSignalDetectionOutput:
    signals: tuple[BuyingSignal, ...]
    uncertainty: tuple[str, ...]
    outcome: BuyingSignalOutcome

    @classmethod
    def from_value(cls, value: object) -> BuyingSignalDetectionOutput:
        if not isinstance(value, dict):
            raise ResearchQualificationContractError("Buying signal output must be an object")
        _require_fields(value, {"signals", "uncertainty", "outcome"})
        signals_value = value["signals"]
        if not isinstance(signals_value, list) or len(signals_value) > 12:
            raise ResearchQualificationContractError("Buying signals must be bounded")
        return cls(
            signals=tuple(BuyingSignal.from_value(item) for item in signals_value),
            uncertainty=_text_list(value["uncertainty"], "uncertainty", 12),
            outcome=_enum(BuyingSignalOutcome, value["outcome"], "outcome"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "signals": [signal.as_dict() for signal in self.signals],
            "uncertainty": list(self.uncertainty),
            "outcome": self.outcome.value,
        }


@dataclass(frozen=True, slots=True)
class QualificationGapInput:
    workspace_id: UUID
    lead_id: UUID
    sources: tuple[AuthoritativeSalesEvidence, ...]


@dataclass(frozen=True, slots=True)
class QualificationGapOutput:
    confirmed_qualification_facts: tuple[SalesEvidenceItem, ...]
    inferred_qualification_context: tuple[SalesEvidenceItem, ...]
    missing_information: tuple[SalesEvidenceItem, ...]
    conflicting_information: tuple[str, ...]
    disqualification_evidence: tuple[SalesEvidenceItem, ...]
    recommended_next_information: tuple[str, ...]
    outcome: QualificationGapOutcome

    def as_dict(self) -> dict[str, object]:
        return {
            "confirmed_qualification_facts": [
                item.as_dict() for item in self.confirmed_qualification_facts
            ],
            "inferred_qualification_context": [
                item.as_dict() for item in self.inferred_qualification_context
            ],
            "missing_information": [item.as_dict() for item in self.missing_information],
            "conflicting_information": list(self.conflicting_information),
            "disqualification_evidence": [
                item.as_dict() for item in self.disqualification_evidence
            ],
            "recommended_next_information": list(self.recommended_next_information),
            "outcome": self.outcome.value,
        }


@dataclass(frozen=True, slots=True)
class ExpertiseExecutionResult:
    outcome: StrEnum
    validation_outcome: ExpertiseValidationOutcome
    validation_reason: str
    ai_invoked: bool
    structured_result: dict[str, object]


class AccountResearchOutputValidator:
    def validate(
        self,
        value: object,
        source: AccountResearchInput | None = None,
    ) -> AccountResearchOutput:
        if not isinstance(value, AccountResearchOutput) or source is None:
            raise ResearchQualificationValidationError("Account research requires typed input")
        _validate_evidence(
            value.confirmed_facts,
            SalesEvidenceClassification.CONFIRMED,
            source.sources,
        )
        _validate_evidence(
            value.inferred_context,
            SalesEvidenceClassification.INFERENCE,
            source.sources,
        )
        _validate_evidence(
            value.unknowns,
            SalesEvidenceClassification.UNKNOWN,
            source.sources,
        )
        _validate_evidence(
            value.potential_needs,
            SalesEvidenceClassification.INFERENCE,
            source.sources,
        )
        company_source = next(
            item for item in source.sources if item.source_reference == "lead.company_name"
        )
        if not any(
            item.source_reference == company_source.source_reference
            and item.claim == company_source.claim
            for item in value.confirmed_facts
        ):
            raise ResearchQualificationValidationError(
                "Account research omitted the authoritative company identity"
            )
        corpus = _source_corpus(source.sources)
        if _UNSUPPORTED_COMPANY_FACT.search(value.company_summary):
            raise ResearchQualificationValidationError(
                "Company summary contains an unsupported company fact"
            )
        if not set(_NUMBER.findall(value.company_summary)).issubset(
            set(_NUMBER.findall(corpus))
        ):
            raise ResearchQualificationValidationError(
                "Company summary contains an unsupported number"
            )
        return value


class BuyingSignalOutputValidator:
    def validate(
        self,
        value: object,
        source: AccountResearchInput | None = None,
    ) -> BuyingSignalDetectionOutput:
        if not isinstance(value, BuyingSignalDetectionOutput) or source is None:
            raise ResearchQualificationValidationError("Buying signals require typed input")
        expected = _detected_signal_map(source.sources)
        emitted: set[BuyingSignalType] = set()
        for signal in value.signals:
            if signal.signal_type in emitted:
                raise ResearchQualificationValidationError("Buying signal is duplicated")
            emitted.add(signal.signal_type)
            references = expected.get(signal.signal_type)
            if references is None or not signal.supporting_evidence:
                raise ResearchQualificationValidationError("Buying signal has no support")
            if set(signal.supporting_evidence) != set(references):
                raise ResearchQualificationValidationError(
                    "Buying signal evidence is incomplete or unsupported"
                )
            if signal.strength is not _signal_strength(signal.signal_type):
                raise ResearchQualificationValidationError("Buying signal strength is invalid")
        if emitted != set(expected):
            raise ResearchQualificationValidationError("Supported buying signals were omitted")
        correct_outcome = (
            BuyingSignalOutcome.SIGNALS_DETECTED
            if value.signals
            else BuyingSignalOutcome.NO_SUPPORTED_SIGNALS
        )
        if value.outcome is not correct_outcome:
            raise ResearchQualificationValidationError("Buying signal outcome is inconsistent")
        return value


class QualificationGapOutputValidator:
    def validate(
        self,
        value: object,
        source: QualificationGapInput | None = None,
    ) -> QualificationGapOutput:
        if not isinstance(value, QualificationGapOutput) or source is None:
            raise ResearchQualificationValidationError("Qualification gap requires typed input")
        _validate_evidence(
            value.confirmed_qualification_facts,
            SalesEvidenceClassification.CONFIRMED,
            source.sources,
        )
        _validate_evidence(
            value.inferred_qualification_context,
            SalesEvidenceClassification.INFERENCE,
            source.sources,
        )
        _validate_evidence(
            value.missing_information,
            SalesEvidenceClassification.UNKNOWN,
            source.sources,
        )
        _validate_evidence(
            value.disqualification_evidence,
            SalesEvidenceClassification.CONFIRMED,
            source.sources,
        )
        if len(value.recommended_next_information) > 3:
            raise ResearchQualificationValidationError(
                "Qualification recommendations are not bounded"
            )
        for item in value.disqualification_evidence:
            if _DISQUALIFICATION_SIGNAL.search(item.claim) is None:
                raise ResearchQualificationValidationError(
                    "Disqualification is not supported by explicit evidence"
                )
        if (
            value.outcome is QualificationGapOutcome.LIKELY_UNQUALIFIED
            and not value.disqualification_evidence
        ):
            raise ResearchQualificationValidationError(
                "Likely unqualified requires explicit disqualification evidence"
            )
        if (
            value.outcome is QualificationGapOutcome.HUMAN_REVIEW
            and not value.conflicting_information
        ):
            raise ResearchQualificationValidationError(
                "Human review requires a preserved conflict"
            )
        return value


def build_account_research_input(
    workspace_id: UUID,
    lead: Lead,
    history: list[ConversationMessage],
    workspace_instructions: str | None,
) -> AccountResearchInput:
    sources: list[AuthoritativeSalesEvidence] = [
        _source("lead.company_name", SalesEvidenceSourceType.LEAD_RECORD, lead.company_name),
    ]
    optional_fields = {
        "lead.job_title": lead.job_title,
        "lead.website": lead.website,
        "lead.source": lead.source,
        "lead.notes": lead.notes,
    }
    for reference, claim in optional_fields.items():
        if isinstance(claim, str) and claim.strip():
            sources.append(_source(reference, SalesEvidenceSourceType.LEAD_RECORD, claim))
    if lead.email or lead.phone:
        sources.append(
            _source(
                "lead.direct_contact_available",
                SalesEvidenceSourceType.LEAD_RECORD,
                "A direct contact channel is available",
            )
        )
    for message in history[-20:]:
        if message.direction != "inbound" or not message.content.strip():
            continue
        sources.append(
            AuthoritativeSalesEvidence(
                source_reference=f"conversation.{message.id}",
                source_type=SalesEvidenceSourceType.CONVERSATION,
                claim=message.content.strip()[:500],
                captured_at=message.created_at.isoformat(),
            )
        )
    return AccountResearchInput(
        workspace_id=workspace_id,
        lead_id=lead.id,
        sources=tuple(sources),
        workspace_instructions=workspace_instructions,
    )


def safe_account_research_output(source: AccountResearchInput) -> AccountResearchOutput:
    company = next(item for item in source.sources if item.source_reference == "lead.company_name")
    confirmed = tuple(
        SalesEvidenceItem(
            SalesEvidenceClassification.CONFIRMED,
            item.claim,
            item.source_type,
            item.source_reference,
            item.captured_at,
        )
        for item in source.sources
    )
    unknown_claims = (
        "industry",
        "employee count",
        "revenue",
        "funding",
        "technology stack",
    )
    unknowns = tuple(
        SalesEvidenceItem(
            SalesEvidenceClassification.UNKNOWN,
            claim,
            SalesEvidenceSourceType.MISSING,
        )
        for claim in unknown_claims
        if not _source_mentions(source.sources, claim)
    )
    summary = (
        f"HIRI records identify the company as {company.claim}. "
        "No external research was performed; unavailable company facts remain unknown."
    )
    inferred_context = tuple(
        SalesEvidenceItem(
            SalesEvidenceClassification.INFERENCE,
            f"The supplied context may indicate relevant business needs: {item.claim}",
            item.source_type,
            item.source_reference,
            item.captured_at,
        )
        for item in source.sources
        if item.source_reference == "lead.notes"
        or item.source_type is SalesEvidenceSourceType.CONVERSATION
    )
    conflict = _TIMELINE_SOON.search(_source_corpus(source.sources)) and (
        _TIMELINE_LATER.search(_source_corpus(source.sources))
    )
    research_gaps = [item.claim for item in unknowns]
    if conflict:
        research_gaps.append("Conflicting timing information requires human review")
    return AccountResearchOutput(
        company_summary=summary,
        confirmed_facts=confirmed,
        inferred_context=inferred_context,
        unknowns=unknowns,
        potential_needs=inferred_context,
        research_gaps=tuple(research_gaps),
        outcome=(
            AccountResearchOutcome.HUMAN_REVIEW
            if conflict
            else (
                AccountResearchOutcome.LIMITED_CONTEXT
                if unknowns
                else AccountResearchOutcome.CONTEXT_RESEARCHED
            )
        ),
    )


def detect_buying_signals(
    source: AccountResearchInput,
) -> BuyingSignalDetectionOutput:
    detected = _detected_signal_map(source.sources)
    signals = tuple(
        BuyingSignal(
            signal_type=kind,
            strength=_signal_strength(kind),
            supporting_evidence=references,
        )
        for kind, references in sorted(detected.items(), key=lambda item: item[0].value)
    )
    return BuyingSignalDetectionOutput(
        signals=signals,
        uncertainty=(
            ()
            if signals
            else ("No supported buying signal is present in the available HIRI context.",)
        ),
        outcome=(
            BuyingSignalOutcome.SIGNALS_DETECTED
            if signals
            else BuyingSignalOutcome.NO_SUPPORTED_SIGNALS
        ),
    )


def build_qualification_gap_input(
    workspace_id: UUID,
    lead: Lead,
    research: dict[str, object],
) -> QualificationGapInput:
    sources: list[AuthoritativeSalesEvidence] = []
    if lead.job_title:
        sources.append(
            _source("lead.job_title", SalesEvidenceSourceType.LEAD_RECORD, lead.job_title)
        )
    if lead.email or lead.phone:
        sources.append(
            _source(
                "lead.direct_contact_available",
                SalesEvidenceSourceType.LEAD_RECORD,
                "A direct contact channel is available",
            )
        )
    evidence = research.get("evidence", [])
    if isinstance(evidence, list):
        for index, value in enumerate(evidence[:50]):
            try:
                item = SalesEvidenceItem.from_value(value)
            except SalesEvidenceContractError:
                continue
            if item.classification is SalesEvidenceClassification.UNKNOWN:
                continue
            sources.append(
                AuthoritativeSalesEvidence(
                    source_reference=item.source_reference
                    or f"lead_research.legacy.{index}",
                    source_type=SalesEvidenceSourceType.LEAD_RESEARCH,
                    claim=item.claim,
                    captured_at=item.captured_at,
                )
            )
    return QualificationGapInput(workspace_id, lead.id, tuple(sources))


def detect_qualification_gaps(source: QualificationGapInput) -> QualificationGapOutput:
    confirmed: list[SalesEvidenceItem] = []
    inferred: list[SalesEvidenceItem] = []
    disqualification: list[SalesEvidenceItem] = []
    for item in source.sources:
        evidence = SalesEvidenceItem(
            SalesEvidenceClassification.CONFIRMED,
            item.claim,
            item.source_type,
            item.source_reference,
            item.captured_at,
        )
        if item.source_reference in {"lead.job_title", "lead.direct_contact_available"}:
            confirmed.append(evidence)
        if item.claim.startswith("Buying signal:"):
            inferred.append(
                SalesEvidenceItem(
                    SalesEvidenceClassification.INFERENCE,
                    item.claim,
                    item.source_type,
                    item.source_reference,
                    item.captured_at,
                )
            )
        if _DISQUALIFICATION_SIGNAL.search(item.claim):
            disqualification.append(evidence)

    corpus = _source_corpus(source.sources)
    missing_claims: list[str] = []
    if _AUTHORITY_SIGNAL.search(corpus) is None:
        missing_claims.append("decision authority")
    if _PRICING_SIGNAL.search(corpus) is None:
        missing_claims.append("commercial budget")
    missing = tuple(
        SalesEvidenceItem(
            SalesEvidenceClassification.UNKNOWN,
            claim,
            SalesEvidenceSourceType.MISSING,
        )
        for claim in missing_claims
    )
    conflict = (
        ("Conflicting timeline statements remain unresolved.",)
        if _TIMELINE_SOON.search(corpus) and _TIMELINE_LATER.search(corpus)
        else ()
    )
    if conflict:
        outcome = QualificationGapOutcome.HUMAN_REVIEW
    elif disqualification:
        outcome = QualificationGapOutcome.LIKELY_UNQUALIFIED
    elif missing:
        outcome = QualificationGapOutcome.MORE_INFORMATION_NEEDED
    else:
        outcome = QualificationGapOutcome.SUFFICIENT_FOR_CURRENT_STAGE
    return QualificationGapOutput(
        confirmed_qualification_facts=tuple(confirmed),
        inferred_qualification_context=tuple(inferred),
        missing_information=missing,
        conflicting_information=conflict,
        disqualification_evidence=tuple(disqualification),
        recommended_next_information=tuple(item.claim for item in missing[:3]),
        outcome=outcome,
    )


def account_research_components(
    definition: AgentSkillDefinition,
) -> ResolvedAgentSkillComponents:
    return _components(
        definition,
        AccountResearchInput,
        AccountResearchOutput,
        AccountResearchOutputValidator(),
    )


def buying_signal_components(
    definition: AgentSkillDefinition,
) -> ResolvedAgentSkillComponents:
    return _components(
        definition,
        AccountResearchInput,
        BuyingSignalDetectionOutput,
        BuyingSignalOutputValidator(),
    )


def qualification_gap_components(
    definition: AgentSkillDefinition,
) -> ResolvedAgentSkillComponents:
    return _components(
        definition,
        QualificationGapInput,
        QualificationGapOutput,
        QualificationGapOutputValidator(),
    )


def account_execution_result(
    output: AccountResearchOutput,
    *,
    ai_invoked: bool,
    rejected: bool = False,
) -> ExpertiseExecutionResult:
    return ExpertiseExecutionResult(
        outcome=output.outcome,
        validation_outcome=(
            ExpertiseValidationOutcome.REJECTED
            if rejected
            else ExpertiseValidationOutcome.ACCEPTED
        ),
        validation_reason=("generated_output_rejected" if rejected else "grounded_output_accepted"),
        ai_invoked=ai_invoked,
        structured_result={
            "company_summary": output.company_summary,
            "confirmed_facts": [item.as_dict() for item in output.confirmed_facts],
            "inferred_context": [item.as_dict() for item in output.inferred_context],
            "unknowns": [item.as_dict() for item in output.unknowns],
            "potential_needs": [item.as_dict() for item in output.potential_needs],
            "research_gaps": list(output.research_gaps),
        },
    )


def buying_signal_execution_result(
    output: BuyingSignalDetectionOutput,
) -> ExpertiseExecutionResult:
    return ExpertiseExecutionResult(
        outcome=output.outcome,
        validation_outcome=ExpertiseValidationOutcome.ACCEPTED,
        validation_reason="deterministic_evidence_validated",
        ai_invoked=False,
        structured_result=output.as_dict(),
    )


def qualification_gap_execution_result(
    output: QualificationGapOutput,
) -> ExpertiseExecutionResult:
    return ExpertiseExecutionResult(
        outcome=output.outcome,
        validation_outcome=ExpertiseValidationOutcome.ACCEPTED,
        validation_reason="deterministic_evidence_validated",
        ai_invoked=False,
        structured_result=output.as_dict(),
    )


def persisted_research_evidence(
    account: AccountResearchOutput,
    buying: BuyingSignalDetectionOutput,
    source: AccountResearchInput,
) -> list[dict[str, str | None]]:
    evidence = [
        item.as_dict()
        for items in (
            account.confirmed_facts,
            account.inferred_context,
            account.unknowns,
            account.potential_needs,
        )
        for item in items
    ]
    source_map = {item.source_reference: item for item in source.sources}
    for signal in buying.signals:
        for reference in signal.supporting_evidence:
            supporting = source_map[reference]
            evidence.append(
                SalesEvidenceItem(
                    SalesEvidenceClassification.INFERENCE,
                    f"Buying signal: {signal.signal_type.value}",
                    SalesEvidenceSourceType.LEAD_RESEARCH,
                    supporting.source_reference,
                    supporting.captured_at,
                ).as_dict()
            )
            evidence.append(
                SalesEvidenceItem(
                    SalesEvidenceClassification.CONFIRMED,
                    supporting.claim,
                    supporting.source_type,
                    supporting.source_reference,
                    supporting.captured_at,
                ).as_dict()
            )
    return _deduplicated_evidence(evidence)


def _detected_signal_map(
    sources: tuple[AuthoritativeSalesEvidence, ...],
) -> dict[BuyingSignalType, tuple[str, ...]]:
    patterns = {
        BuyingSignalType.PRICING_INTEREST: _PRICING_SIGNAL,
        BuyingSignalType.IMPLEMENTATION_QUESTION: _IMPLEMENTATION_SIGNAL,
        BuyingSignalType.INTEGRATION_QUESTION: _INTEGRATION_SIGNAL,
        BuyingSignalType.DECISION_MAKER_INVOLVEMENT: _AUTHORITY_SIGNAL,
        BuyingSignalType.DEMO_OR_CONTACT_REQUEST: _DEMO_SIGNAL,
        BuyingSignalType.EXPLICIT_BUSINESS_PAIN: _BUSINESS_PAIN_SIGNAL,
        BuyingSignalType.PURCHASE_INTENT: _PURCHASE_INTENT_SIGNAL,
    }
    detected: dict[BuyingSignalType, tuple[str, ...]] = {}
    for kind, pattern in patterns.items():
        references = tuple(
            source.source_reference for source in sources if pattern.search(source.claim)
        )
        if references:
            detected[kind] = references
    timeline_references = tuple(
        source.source_reference
        for source in sources
        if _TIMELINE_SOON.search(source.claim) or _TIMELINE_LATER.search(source.claim)
    )
    if timeline_references:
        detected[BuyingSignalType.TIMELINE_STATEMENT] = timeline_references
    conversation = tuple(
        source.source_reference
        for source in sources
        if source.source_type is SalesEvidenceSourceType.CONVERSATION
    )
    if len(conversation) >= 2:
        detected[BuyingSignalType.REPEATED_ENGAGEMENT] = conversation
    return detected


def _signal_strength(signal_type: BuyingSignalType) -> BuyingSignalStrength:
    if signal_type in {
        BuyingSignalType.DEMO_OR_CONTACT_REQUEST,
        BuyingSignalType.PURCHASE_INTENT,
    }:
        return BuyingSignalStrength.HIGH
    if signal_type in {
        BuyingSignalType.PRICING_INTEREST,
        BuyingSignalType.IMPLEMENTATION_QUESTION,
        BuyingSignalType.INTEGRATION_QUESTION,
        BuyingSignalType.TIMELINE_STATEMENT,
        BuyingSignalType.DECISION_MAKER_INVOLVEMENT,
    }:
        return BuyingSignalStrength.MEDIUM
    return BuyingSignalStrength.LOW


def _validate_evidence(
    items: tuple[SalesEvidenceItem, ...],
    expected: SalesEvidenceClassification,
    sources: tuple[AuthoritativeSalesEvidence, ...],
) -> None:
    for item in items:
        if item.classification is not expected:
            raise ResearchQualificationValidationError("Evidence classification was promoted")
        if expected is SalesEvidenceClassification.UNKNOWN:
            if item.source_type is not SalesEvidenceSourceType.MISSING:
                raise ResearchQualificationValidationError("Unknown evidence claims a source")
            continue
        if item.source_reference is None:
            raise ResearchQualificationValidationError("Supported evidence has no reference")
        matching_sources = tuple(
            source
            for source in sources
            if source.source_reference == item.source_reference
            and source.source_type is item.source_type
        )
        if not matching_sources:
            raise ResearchQualificationValidationError("Evidence source is not authoritative")
        if expected is SalesEvidenceClassification.CONFIRMED and not any(
            item.claim == source.claim for source in matching_sources
        ):
            raise ResearchQualificationValidationError("Confirmed claim is not authoritative")
        if not any(item.captured_at == source.captured_at for source in matching_sources):
            raise ResearchQualificationValidationError("Evidence timestamp is not authoritative")


def _components(
    definition: AgentSkillDefinition,
    input_contract: type[object],
    output_contract: type[object],
    validator: (
        AccountResearchOutputValidator
        | BuyingSignalOutputValidator
        | QualificationGapOutputValidator
    ),
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


def _source(
    reference: str,
    source_type: SalesEvidenceSourceType,
    claim: str,
) -> AuthoritativeSalesEvidence:
    return AuthoritativeSalesEvidence(reference, source_type, claim.strip()[:500])


def _source_corpus(sources: tuple[AuthoritativeSalesEvidence, ...]) -> str:
    return "\n".join(source.claim for source in sources)


def _source_mentions(
    sources: tuple[AuthoritativeSalesEvidence, ...],
    phrase: str,
) -> bool:
    return phrase.casefold() in _source_corpus(sources).casefold()


def _deduplicated_evidence(
    values: list[dict[str, str | None]],
) -> list[dict[str, str | None]]:
    seen: set[tuple[tuple[str, str | None], ...]] = set()
    result: list[dict[str, str | None]] = []
    for value in values:
        identity = tuple(sorted(value.items()))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(value)
    return result[:100]


def _json_object(raw: str) -> dict[str, object]:
    normalized = raw.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*|\s*```$", "", normalized, flags=re.IGNORECASE)
    try:
        value = json.loads(normalized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ResearchQualificationContractError("Skill output must be one JSON object") from exc
    if not isinstance(value, dict):
        raise ResearchQualificationContractError("Skill output must be an object")
    return value


def _require_fields(value: dict[str, object], required: set[str]) -> None:
    if set(value) != required:
        raise ResearchQualificationContractError("Skill output fields are invalid")


def _evidence_list(value: object) -> tuple[SalesEvidenceItem, ...]:
    if not isinstance(value, list) or len(value) > 50:
        raise ResearchQualificationContractError("Evidence list is invalid")
    try:
        return tuple(SalesEvidenceItem.from_value(item) for item in value)
    except SalesEvidenceContractError as exc:
        raise ResearchQualificationContractError(str(exc)) from exc


def _text_list(value: object, field: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ResearchQualificationContractError(f"{field} must be bounded")
    return tuple(_required_text(item, field, 500) for item in value)


def _required_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ResearchQualificationContractError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ResearchQualificationContractError(f"{field} is invalid")
    return normalized


def _enum(enum_type: type[StrEnum], value: object, field: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ResearchQualificationContractError(f"{field} is invalid") from exc
