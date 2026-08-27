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

QUALIFICATION_FACT_EVIDENCE_TYPE = "qualification_fact"
QUALIFICATION_FACT_EVIDENCE_SCHEMA_VERSION = 1
MAX_RESEARCH_EVIDENCE_ITEMS = 100

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
