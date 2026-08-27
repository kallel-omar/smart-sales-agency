"""Deterministic qualification-gap collection contracts for Sales conversations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.departments.sales.evidence import (
    SalesEvidenceClassification,
    SalesEvidenceSourceType,
)
from app.departments.sales.icp_scoring import ICPFact, ICPReasonCode
from app.departments.sales.playbook import (
    SalesPlaybookCriterionImportance,
    SalesPlaybookCriterionType,
    SalesPlaybookV1,
)

QUALIFICATION_COLLECTION_POLICY_VERSION = "sales.qualification_collection.v1"
MAX_QUALIFICATION_COLLECTION_ITEMS = 10

_COMPANY_SIZE_PATTERNS = (
    re.compile(
        r"\b(?:we have|our (?:company|team) has|team of)\s+([0-9]{1,9})\s+"
        r"(?:employees|people|team members|staff)\b",
        re.IGNORECASE,
    ),
)
_CHANNEL_VOLUME_PATTERNS = (
    re.compile(
        r"\b(?:we receive|we handle|we get|our volume is)\s+([0-9]{1,12})\s+"
        r"(?:customer\s+)?(?:messages|leads|inquiries|conversations)\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class SalesQualificationContext:
    """One trusted, bounded missing field supplied to a normal Sales turn."""

    key: str
    description: str
    criterion_type: SalesPlaybookCriterionType | None = None

    def render(self) -> str:
        criterion = (
            f" ({self.criterion_type.value})" if self.criterion_type is not None else ""
        )
        return (
            "Pending qualification context: answer the customer's current question first. "
            "When contextually appropriate, ask at most one concise question for this missing "
            f"item and do not use questionnaire wording: {self.key}{criterion} — "
            f"{self.description}"
        )


def build_qualification_collection_plan(
    *,
    qualification_work_item_id: str,
    playbook: SalesPlaybookV1,
    icp_assessment: object,
    qualification_policy: object,
) -> dict[str, object] | None:
    """Build a bounded plan only for an authoritative needs-more-information result."""

    if (
        not isinstance(icp_assessment, dict)
        or icp_assessment.get("status") != "assessed"
        or not isinstance(qualification_policy, dict)
        or qualification_policy.get("decision") != "needs_more_information"
    ):
        return None

    required_information_by_key = {
        item.key: item for item in playbook.qualification.required_information
    }
    required_criteria_by_key = {
        item.key: item
        for item in playbook.icp.criteria
        if item.importance is SalesPlaybookCriterionImportance.REQUIRED
    }

    missing_information: list[dict[str, object]] = []
    raw_information = icp_assessment.get("required_information_gaps")
    if isinstance(raw_information, list):
        for value in raw_information:
            if not isinstance(value, dict) or not isinstance(value.get("key"), str):
                continue
            configured = required_information_by_key.get(value["key"])
            if configured is None:
                continue
            criterion_type = _criterion_type_from_key(configured.key)
            missing_information.append(
                {
                    "key": configured.key,
                    "description": configured.description,
                    "criterion_type": (
                        criterion_type.value if criterion_type is not None else None
                    ),
                    "reason_code": _safe_reason_code(value.get("reason_code")),
                }
            )

    unresolved_criteria: list[dict[str, object]] = []
    raw_criteria = icp_assessment.get("unknown_criteria")
    if isinstance(raw_criteria, list):
        for value in raw_criteria:
            if not isinstance(value, dict) or not isinstance(value.get("rule_key"), str):
                continue
            configured = required_criteria_by_key.get(value["rule_key"])
            if configured is None:
                continue
            unresolved_criteria.append(
                {
                    "key": configured.key,
                    "description": f"Confirm the required {configured.criterion_type.value}",
                    "criterion_type": configured.criterion_type.value,
                    "reason_code": _safe_reason_code(value.get("reason_code")),
                }
            )

    missing_information = missing_information[:MAX_QUALIFICATION_COLLECTION_ITEMS]
    remaining = MAX_QUALIFICATION_COLLECTION_ITEMS - len(missing_information)
    unresolved_criteria = unresolved_criteria[:remaining]
    if not missing_information and not unresolved_criteria:
        return None
    reason_codes = qualification_policy.get("reason_codes")
    return {
        "policy_version": QUALIFICATION_COLLECTION_POLICY_VERSION,
        "qualification_work_item_id": qualification_work_item_id,
        "missing_required_information": missing_information,
        "unresolved_required_criteria": unresolved_criteria,
        "reason_codes": [
            value
            for value in (reason_codes if isinstance(reason_codes, list) else [])
            if isinstance(value, str)
        ][:MAX_QUALIFICATION_COLLECTION_ITEMS],
        "collection_status": "pending",
    }


def first_collection_context(plan: object) -> SalesQualificationContext | None:
    """Return the first safe missing item; prompt composition never receives the full plan."""

    if not isinstance(plan, dict) or plan.get("collection_status") != "pending":
        return None
    for field in ("missing_required_information", "unresolved_required_criteria"):
        values = plan.get(field)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            description = item.get("description")
            if not isinstance(key, str) or not isinstance(description, str):
                continue
            try:
                criterion_type = (
                    SalesPlaybookCriterionType(item["criterion_type"])
                    if item.get("criterion_type") is not None
                    else None
                )
            except (TypeError, ValueError):
                criterion_type = None
            return SalesQualificationContext(key, description, criterion_type)
    return None


def conversation_qualification_facts(
    plan: object,
    customer_message: str,
    *,
    source_reference: str,
) -> tuple[ICPFact, ...]:
    """Extract only explicit numeric self-reports requested by the pending plan."""

    if not isinstance(plan, dict) or plan.get("collection_status") != "pending":
        return ()
    items: list[dict[str, object]] = []
    for field in ("missing_required_information", "unresolved_required_criteria"):
        values = plan.get(field)
        if isinstance(values, list):
            items.extend(value for value in values if isinstance(value, dict))

    facts: list[ICPFact] = []
    identities: set[tuple[str, str]] = set()
    for item in items[:MAX_QUALIFICATION_COLLECTION_ITEMS]:
        key = item.get("key")
        try:
            criterion_type = SalesPlaybookCriterionType(item.get("criterion_type"))
        except (TypeError, ValueError):
            continue
        if not isinstance(key, str):
            continue
        value = _explicit_numeric_value(criterion_type, customer_message)
        if value is None:
            continue
        identity = (key, criterion_type.value)
        if identity in identities:
            continue
        identities.add(identity)
        facts.append(
            ICPFact(
                key=key,
                criterion_type=criterion_type,
                classification=SalesEvidenceClassification.CONFIRMED,
                value=value,
                source_type=SalesEvidenceSourceType.CONVERSATION,
                source_reference=source_reference,
            )
        )
    return tuple(facts)


def conversation_facts_for_playbook(
    playbook: SalesPlaybookV1,
    customer_message: str,
    *,
    source_reference: str,
) -> tuple[ICPFact, ...]:
    """Rebuild allowed conversation facts from an exact persisted message."""

    requested: list[tuple[str, SalesPlaybookCriterionType]] = []
    for rule in (*playbook.icp.criteria, *playbook.icp.disqualifiers):
        requested.append((rule.criterion_type.value, rule.criterion_type))
    for item in playbook.qualification.required_information:
        criterion_type = _criterion_type_from_key(item.key)
        if criterion_type is not None:
            requested.append((item.key, criterion_type))

    facts: list[ICPFact] = []
    identities: set[tuple[str, str]] = set()
    for key, criterion_type in requested:
        identity = (key, criterion_type.value)
        if identity in identities:
            continue
        identities.add(identity)
        value = _explicit_numeric_value(criterion_type, customer_message)
        if value is None:
            continue
        facts.append(
            ICPFact(
                key=key,
                criterion_type=criterion_type,
                classification=SalesEvidenceClassification.CONFIRMED,
                value=value,
                source_type=SalesEvidenceSourceType.CONVERSATION,
                source_reference=source_reference,
            )
        )
    return tuple(facts)


def _explicit_numeric_value(
    criterion_type: SalesPlaybookCriterionType,
    message: str,
) -> int | None:
    patterns = {
        SalesPlaybookCriterionType.COMPANY_SIZE: _COMPANY_SIZE_PATTERNS,
        SalesPlaybookCriterionType.CHANNEL_VOLUME: _CHANNEL_VOLUME_PATTERNS,
    }.get(criterion_type, ())
    for pattern in patterns:
        match = pattern.search(message)
        if match is not None:
            return int(match.group(1))
    return None


def _criterion_type_from_key(key: str) -> SalesPlaybookCriterionType | None:
    try:
        return SalesPlaybookCriterionType(key)
    except ValueError:
        return None


def _safe_reason_code(value: object) -> str:
    try:
        return ICPReasonCode(value).value
    except (TypeError, ValueError):
        return ICPReasonCode.EVIDENCE_MISSING.value
