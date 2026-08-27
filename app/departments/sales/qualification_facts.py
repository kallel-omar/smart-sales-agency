"""Pure adapter from persisted structured research evidence to ICP facts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.departments.sales.evidence import (
    SalesEvidenceClassification,
    SalesEvidenceSourceType,
)
from app.departments.sales.icp_scoring import (
    ICPFact,
    ICPScoringContractError,
)
from app.departments.sales.playbook import (
    SalesPlaybookCriterionType,
    SalesPlaybookV1,
)
from app.departments.sales.research_qualification_expertise import (
    AccountResearchInput,
    AccountResearchOutput,
    BuyingSignalDetectionOutput,
    BuyingSignalType,
)

QUALIFICATION_FACT_EVIDENCE_TYPE = "qualification_fact"
QUALIFICATION_FACT_EVIDENCE_SCHEMA_VERSION = 1
MAX_RESEARCH_EVIDENCE_ITEMS = 100
MAX_PRODUCED_QUALIFICATION_FACTS = 20

_QUALIFICATION_FACT_FIELDS = {
    "type",
    "schema_version",
    "key",
    "criterion_type",
    "classification",
    "value",
}


@dataclass(frozen=True, slots=True)
class PersistedQualificationEvidence:
    """Bounded evidence copied from one already-scoped LeadResearch record."""

    research_id: UUID
    items: tuple[object, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.research_id, UUID) or not isinstance(self.items, tuple):
            raise ICPScoringContractError("Persisted qualification evidence is invalid")


def produce_lead_research_qualification_evidence(
    playbook: SalesPlaybookV1,
    account: AccountResearchOutput,
    buying: BuyingSignalDetectionOutput,
    source: AccountResearchInput,
) -> list[dict[str, object]]:
    """Produce bounded facts from validated research without trusting model labels."""

    if not isinstance(playbook, SalesPlaybookV1):
        raise ICPScoringContractError("Qualification evidence requires SalesPlaybookV1")
    if SalesPlaybookCriterionType.BUSINESS_PROBLEM not in _requested_criterion_types(
        playbook
    ):
        return []

    source_map = {item.source_reference: item for item in source.sources}
    produced: list[dict[str, object]] = []
    explicit_problem_references = {
        reference
        for signal in buying.signals
        if signal.signal_type is BuyingSignalType.EXPLICIT_BUSINESS_PAIN
        for reference in signal.supporting_evidence
    }
    for reference in sorted(explicit_problem_references):
        supporting = source_map.get(reference)
        if supporting is None:
            continue
        envelope = _qualification_fact_envelope(
            classification=SalesEvidenceClassification.CONFIRMED,
            value=supporting.claim,
            source_type=supporting.source_type,
            source_reference=supporting.source_reference,
        )
        if envelope is not None:
            produced.append(envelope)

    for candidate in account.potential_needs:
        if candidate.classification is not SalesEvidenceClassification.INFERENCE:
            continue
        supporting = source_map.get(candidate.source_reference or "")
        if supporting is None or supporting.source_type is not candidate.source_type:
            continue
        envelope = _qualification_fact_envelope(
            classification=SalesEvidenceClassification.INFERENCE,
            value=candidate.claim,
            source_type=candidate.source_type,
            source_reference=supporting.source_reference,
        )
        if envelope is not None:
            produced.append(envelope)

    deduplicated: list[dict[str, object]] = []
    identities: set[tuple[object, ...]] = set()
    for envelope in produced:
        identity = (
            envelope["criterion_type"],
            envelope["classification"],
            envelope["value"],
        )
        if identity in identities:
            continue
        identities.add(identity)
        deduplicated.append(envelope)
        if len(deduplicated) >= MAX_PRODUCED_QUALIFICATION_FACTS:
            break
    return deduplicated


def adapt_qualification_facts(
    playbook: SalesPlaybookV1,
    evidence_records: tuple[PersistedQualificationEvidence, ...],
) -> tuple[ICPFact, ...]:
    """Return requested typed facts; malformed and unavailable facts stay unknown."""

    if not isinstance(playbook, SalesPlaybookV1):
        raise ICPScoringContractError("Qualification facts require SalesPlaybookV1")
    requested_types = _requested_criterion_types(playbook)
    requested_information = {
        item.key for item in playbook.qualification.required_information
    }
    facts: list[ICPFact] = []
    inspected = 0
    for record in evidence_records:
        if not isinstance(record, PersistedQualificationEvidence):
            raise ICPScoringContractError("Persisted qualification evidence is invalid")
        for index, value in enumerate(record.items):
            if inspected >= MAX_RESEARCH_EVIDENCE_ITEMS:
                break
            inspected += 1
            fact = _fact_from_research_evidence(record.research_id, index, value)
            if fact is None:
                continue
            if (
                fact.criterion_type in requested_types
                or fact.key in requested_information
            ):
                facts.append(fact)
        if inspected >= MAX_RESEARCH_EVIDENCE_ITEMS:
            break

    represented_types = {fact.criterion_type for fact in facts}
    facts.extend(
        _unknown_fact(criterion_type)
        for criterion_type in sorted(requested_types - represented_types, key=str)
    )
    return tuple(facts)


def _fact_from_research_evidence(
    research_id: UUID,
    index: int,
    value: object,
) -> ICPFact | None:
    if not isinstance(value, dict) or set(value) != _QUALIFICATION_FACT_FIELDS:
        return None
    if (
        value.get("type") != QUALIFICATION_FACT_EVIDENCE_TYPE
        or value.get("schema_version") != QUALIFICATION_FACT_EVIDENCE_SCHEMA_VERSION
    ):
        return None
    try:
        classification = SalesEvidenceClassification(value["classification"])
        criterion_type = SalesPlaybookCriterionType(value["criterion_type"])
    except (TypeError, ValueError):
        return None
    try:
        if classification is SalesEvidenceClassification.UNKNOWN:
            return ICPFact(
                key=value["key"],
                criterion_type=criterion_type,
                classification=classification,
                value=value["value"],
                source_type=SalesEvidenceSourceType.MISSING,
            )
        return ICPFact(
            key=value["key"],
            criterion_type=criterion_type,
            classification=classification,
            value=value["value"],
            source_type=SalesEvidenceSourceType.LEAD_RESEARCH,
            source_reference=f"lead_research:{research_id}:evidence:{index}",
        )
    except (ICPScoringContractError, TypeError):
        return None


def _qualification_fact_envelope(
    *,
    classification: SalesEvidenceClassification,
    value: object,
    source_type: SalesEvidenceSourceType,
    source_reference: str,
) -> dict[str, object] | None:
    try:
        fact = ICPFact(
            key=SalesPlaybookCriterionType.BUSINESS_PROBLEM.value,
            criterion_type=SalesPlaybookCriterionType.BUSINESS_PROBLEM,
            classification=classification,
            value=value,
            source_type=source_type,
            source_reference=source_reference,
        )
    except (ICPScoringContractError, TypeError):
        return None
    return {
        "type": QUALIFICATION_FACT_EVIDENCE_TYPE,
        "schema_version": QUALIFICATION_FACT_EVIDENCE_SCHEMA_VERSION,
        "key": fact.key,
        "criterion_type": fact.criterion_type.value,
        "classification": fact.classification.value,
        "value": fact.value,
    }


def _requested_criterion_types(
    playbook: SalesPlaybookV1,
) -> set[SalesPlaybookCriterionType]:
    return {
        rule.criterion_type
        for rule in (*playbook.icp.criteria, *playbook.icp.disqualifiers)
    }


def _unknown_fact(criterion_type: SalesPlaybookCriterionType) -> ICPFact:
    return ICPFact(
        key=criterion_type.value,
        criterion_type=criterion_type,
        classification=SalesEvidenceClassification.UNKNOWN,
        value=None,
        source_type=SalesEvidenceSourceType.MISSING,
    )
