"""Deterministic, evidence-disciplined evaluation of Sales Playbook v1."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.core.agent_skill_execution import (
    AgentSkillComponentResolver,
    AgentSkillContractRegistry,
    AgentSkillExecutionContext,
    AgentSkillValidatorRegistry,
    ResolvedAgentSkillComponents,
)
from app.core.agent_skills import AgentSkillDefinition
from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.departments.sales.evidence import (
    SalesEvidenceClassification,
    SalesEvidenceSourceType,
)
from app.departments.sales.playbook import (
    SALES_PLAYBOOK_CRITERION_REGISTRY,
    SalesPlaybookCriterionImportance,
    SalesPlaybookCriterionOperator,
    SalesPlaybookCriterionType,
    SalesPlaybookCriterionValueKind,
    SalesPlaybookICPCriterion,
    SalesPlaybookICPDisqualifier,
    SalesPlaybookV1,
)

ICP_SCORING_KEY = "icp_scoring"
ICP_SCORING_VERSION = "v1"
MAX_ICP_FACTS = 100

_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ICPFitStatus(StrEnum):
    FIT = "fit"
    NOT_FIT = "not_fit"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    DISQUALIFIED = "disqualified"


class ICPRuleStatus(StrEnum):
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNKNOWN = "unknown"


class ICPRequiredInformationStatus(StrEnum):
    KNOWN = "known"
    GAP = "gap"


class ICPReasonCode(StrEnum):
    CONFIRMED_VALUE_MATCH = "confirmed_value_match"
    CONFIRMED_VALUE_MISMATCH = "confirmed_value_mismatch"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INFERENCE_NOT_CONFIRMED = "inference_not_confirmed"
    EVIDENCE_UNKNOWN = "evidence_unknown"
    EVIDENCE_MISSING = "evidence_missing"
    REQUIRED_INFORMATION_CONFIRMED = "required_information_confirmed"
    REQUIRED_INFORMATION_MISSING = "required_information_missing"
    REQUIRED_INFORMATION_INFERENCE_ONLY = "required_information_inference_only"
    CONFIRMED_DISQUALIFIER_MATCHED = "confirmed_disqualifier_matched"
    REQUIRED_CRITERION_MISMATCH = "required_criterion_mismatch"
    REQUIRED_CRITERION_UNKNOWN = "required_criterion_unknown"
    ALL_REQUIRED_CRITERIA_MATCHED = "all_required_criteria_matched"


class ICPScoringContractError(ValueError):
    """Raised when deterministic ICP input or output is malformed."""


class ICPScoringAuthorizationError(PermissionError):
    """Raised when an execution context cannot run icp_scoring:v1."""


class ICPScoringValidationError(ValueError):
    """Raised when an ICP result differs from deterministic evaluation."""


@dataclass(frozen=True, slots=True)
class ICPFact:
    """One typed fact with its existing HIRI evidence classification."""

    key: str
    criterion_type: SalesPlaybookCriterionType
    classification: SalesEvidenceClassification
    value: str | int | float | None
    source_type: SalesEvidenceSourceType
    source_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or _SAFE_KEY.fullmatch(self.key) is None:
            raise ICPScoringContractError("ICP fact key is invalid")
        if not isinstance(self.criterion_type, SalesPlaybookCriterionType):
            raise ICPScoringContractError("ICP fact criterion type is invalid")
        if not isinstance(self.classification, SalesEvidenceClassification):
            raise ICPScoringContractError("ICP fact classification is invalid")
        if not isinstance(self.source_type, SalesEvidenceSourceType):
            raise ICPScoringContractError("ICP fact source type is invalid")

        if self.classification is SalesEvidenceClassification.UNKNOWN:
            if (
                self.value is not None
                or self.source_type is not SalesEvidenceSourceType.MISSING
                or self.source_reference is not None
            ):
                raise ICPScoringContractError(
                    "Unknown ICP facts cannot claim a value or source"
                )
            return
        if (
            self.source_type is SalesEvidenceSourceType.MISSING
            or not isinstance(self.source_reference, str)
            or not self.source_reference.strip()
            or len(self.source_reference) > 200
        ):
            raise ICPScoringContractError("Supported ICP facts require a safe source")

        specification = SALES_PLAYBOOK_CRITERION_REGISTRY[self.criterion_type]
        if specification.value_kind is SalesPlaybookCriterionValueKind.TEXT:
            if not isinstance(self.value, str):
                raise ICPScoringContractError("Text ICP facts require a text value")
            normalized = _normalize_text(self.value)
            if not normalized or len(normalized) > 200:
                raise ICPScoringContractError("Text ICP fact value is invalid")
            object.__setattr__(self, "value", normalized)
        elif not _is_non_negative_finite_number(self.value):
            raise ICPScoringContractError("Numeric ICP facts require a valid number")


@dataclass(frozen=True, slots=True)
class ICPScoringInput:
    workspace_id: UUID
    lead_id: UUID
    facts: tuple[ICPFact, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, UUID) or not isinstance(self.lead_id, UUID):
            raise ICPScoringContractError("ICP scoring scope is invalid")
        if not isinstance(self.facts, tuple) or len(self.facts) > MAX_ICP_FACTS:
            raise ICPScoringContractError("ICP scoring facts must be a bounded tuple")
        if any(not isinstance(fact, ICPFact) for fact in self.facts):
            raise ICPScoringContractError("ICP scoring facts are invalid")


@dataclass(frozen=True, slots=True)
class ICPRuleAssessment:
    rule_key: str
    status: ICPRuleStatus
    reason_code: ICPReasonCode
    evidence_references: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "rule_key": self.rule_key,
            "status": self.status.value,
            "reason_code": self.reason_code.value,
            "evidence_references": list(self.evidence_references),
        }


@dataclass(frozen=True, slots=True)
class ICPRequiredInformationAssessment:
    key: str
    status: ICPRequiredInformationStatus
    reason_code: ICPReasonCode
    evidence_references: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "status": self.status.value,
            "reason_code": self.reason_code.value,
            "evidence_references": list(self.evidence_references),
        }


@dataclass(frozen=True, slots=True)
class ICPScoringResult:
    matched_criteria: tuple[ICPRuleAssessment, ...]
    mismatched_criteria: tuple[ICPRuleAssessment, ...]
    unknown_criteria: tuple[ICPRuleAssessment, ...]
    matched_disqualifiers: tuple[ICPRuleAssessment, ...]
    unmatched_disqualifiers: tuple[ICPRuleAssessment, ...]
    unknown_disqualifiers: tuple[ICPRuleAssessment, ...]
    known_required_information: tuple[ICPRequiredInformationAssessment, ...]
    required_information_gaps: tuple[ICPRequiredInformationAssessment, ...]
    fit_status: ICPFitStatus
    reason_codes: tuple[ICPReasonCode, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "matched_criteria": [item.as_dict() for item in self.matched_criteria],
            "mismatched_criteria": [item.as_dict() for item in self.mismatched_criteria],
            "unknown_criteria": [item.as_dict() for item in self.unknown_criteria],
            "matched_disqualifiers": [
                item.as_dict() for item in self.matched_disqualifiers
            ],
            "unmatched_disqualifiers": [
                item.as_dict() for item in self.unmatched_disqualifiers
            ],
            "unknown_disqualifiers": [
                item.as_dict() for item in self.unknown_disqualifiers
            ],
            "known_required_information": [
                item.as_dict() for item in self.known_required_information
            ],
            "required_information_gaps": [
                item.as_dict() for item in self.required_information_gaps
            ],
            "fit_status": self.fit_status.value,
            "reason_codes": [item.value for item in self.reason_codes],
        }


@dataclass(frozen=True, slots=True)
class ICPScoringExecutionResult:
    result: ICPScoringResult
    attribution_identifier: str
    ai_invoked: bool = False


class ICPScoringOutputValidator:
    """Ensure callers cannot substitute a non-deterministic assessment."""

    def validate(
        self,
        value: object,
        source: tuple[SalesPlaybookV1, ICPScoringInput] | None = None,
    ) -> ICPScoringResult:
        if not isinstance(value, ICPScoringResult) or source is None:
            raise ICPScoringValidationError("ICP scoring requires typed input and output")
        playbook, scoring_input = source
        if value != evaluate_icp(playbook, scoring_input):
            raise ICPScoringValidationError("ICP scoring result is not deterministic")
        return value


def icp_scoring_components(
    definition: AgentSkillDefinition,
) -> ResolvedAgentSkillComponents:
    return AgentSkillComponentResolver(
        AgentSkillContractRegistry(
            (
                (definition.input_contract, ICPScoringInput),
                (definition.output_contract, ICPScoringResult),
            )
        ),
        AgentSkillValidatorRegistry(
            ((definition.validator, ICPScoringOutputValidator()),)
        ),
    ).resolve(definition)


def execute_icp_scoring(
    playbook: SalesPlaybookV1,
    source: ICPScoringInput,
    context: AgentSkillExecutionContext,
) -> ICPScoringExecutionResult:
    """Run the tool-free skill after validating its governed execution identity."""

    from app.departments.sales.skills import sales_agent_skill_registry

    definition = sales_agent_skill_registry().resolve(
        ICP_SCORING_KEY,
        ICP_SCORING_VERSION,
    )
    _validate_execution_context(context, definition)
    components = icp_scoring_components(definition)
    if not isinstance(source, components.input_contract):
        raise ICPScoringContractError("ICP scoring input contract is invalid")
    result = evaluate_icp(playbook, source)
    result = components.validator.validate(result, (playbook, source))
    if not isinstance(result, components.output_contract):
        raise ICPScoringValidationError("ICP scoring output contract is invalid")
    return ICPScoringExecutionResult(result, context.attribution_identifier)


def evaluate_icp(
    playbook: SalesPlaybookV1,
    source: ICPScoringInput,
) -> ICPScoringResult:
    """Evaluate v1 rules without an LLM, persistence, or business side effects."""

    if not isinstance(playbook, SalesPlaybookV1):
        raise ICPScoringContractError("ICP scoring requires SalesPlaybookV1")

    criteria = tuple(_assess_rule(rule, source.facts) for rule in playbook.icp.criteria)
    disqualifiers = tuple(
        _assess_rule(rule, source.facts) for rule in playbook.icp.disqualifiers
    )
    required_information = tuple(
        _assess_required_information(item.key, source.facts)
        for item in playbook.qualification.required_information
    )

    matched = tuple(item for item in criteria if item.status is ICPRuleStatus.MATCHED)
    mismatched = tuple(item for item in criteria if item.status is ICPRuleStatus.MISMATCHED)
    unknown = tuple(item for item in criteria if item.status is ICPRuleStatus.UNKNOWN)
    matched_disqualifiers = tuple(
        item for item in disqualifiers if item.status is ICPRuleStatus.MATCHED
    )
    unmatched_disqualifiers = tuple(
        item for item in disqualifiers if item.status is ICPRuleStatus.MISMATCHED
    )
    unknown_disqualifiers = tuple(
        item for item in disqualifiers if item.status is ICPRuleStatus.UNKNOWN
    )
    known_information = tuple(
        item
        for item in required_information
        if item.status is ICPRequiredInformationStatus.KNOWN
    )
    information_gaps = tuple(
        item
        for item in required_information
        if item.status is ICPRequiredInformationStatus.GAP
    )

    required_by_key = {
        rule.key: rule.importance is SalesPlaybookCriterionImportance.REQUIRED
        for rule in playbook.icp.criteria
    }
    required_mismatch = any(required_by_key[item.rule_key] for item in mismatched)
    required_unknown = any(required_by_key[item.rule_key] for item in unknown)
    if matched_disqualifiers:
        fit_status = ICPFitStatus.DISQUALIFIED
        primary_reason = ICPReasonCode.CONFIRMED_DISQUALIFIER_MATCHED
    elif required_mismatch:
        fit_status = ICPFitStatus.NOT_FIT
        primary_reason = ICPReasonCode.REQUIRED_CRITERION_MISMATCH
    elif required_unknown:
        fit_status = ICPFitStatus.INSUFFICIENT_INFORMATION
        primary_reason = ICPReasonCode.REQUIRED_CRITERION_UNKNOWN
    else:
        fit_status = ICPFitStatus.FIT
        primary_reason = ICPReasonCode.ALL_REQUIRED_CRITERIA_MATCHED

    detail_reasons = {
        item.reason_code for item in (*criteria, *disqualifiers, *required_information)
    }
    return ICPScoringResult(
        matched,
        mismatched,
        unknown,
        matched_disqualifiers,
        unmatched_disqualifiers,
        unknown_disqualifiers,
        known_information,
        information_gaps,
        fit_status,
        (primary_reason, *tuple(sorted(detail_reasons, key=lambda item: item.value))),
    )


def _assess_rule(
    rule: SalesPlaybookICPCriterion | SalesPlaybookICPDisqualifier,
    facts: tuple[ICPFact, ...],
) -> ICPRuleAssessment:
    relevant = tuple(fact for fact in facts if fact.criterion_type is rule.criterion_type)
    confirmed = tuple(
        fact
        for fact in relevant
        if fact.classification is SalesEvidenceClassification.CONFIRMED
    )
    references = _references(confirmed)
    if confirmed:
        canonical_values = {_canonical_value(fact.value) for fact in confirmed}
        if len(canonical_values) > 1:
            return ICPRuleAssessment(
                rule.key,
                ICPRuleStatus.UNKNOWN,
                ICPReasonCode.CONFLICTING_EVIDENCE,
                references,
            )
        matched = _matches(rule, confirmed[0].value)
        return ICPRuleAssessment(
            rule.key,
            ICPRuleStatus.MATCHED if matched else ICPRuleStatus.MISMATCHED,
            (
                ICPReasonCode.CONFIRMED_VALUE_MATCH
                if matched
                else ICPReasonCode.CONFIRMED_VALUE_MISMATCH
            ),
            references,
        )
    if any(
        fact.classification is SalesEvidenceClassification.INFERENCE for fact in relevant
    ):
        reason = ICPReasonCode.INFERENCE_NOT_CONFIRMED
    elif relevant:
        reason = ICPReasonCode.EVIDENCE_UNKNOWN
    else:
        reason = ICPReasonCode.EVIDENCE_MISSING
    return ICPRuleAssessment(rule.key, ICPRuleStatus.UNKNOWN, reason)


def _assess_required_information(
    key: str,
    facts: tuple[ICPFact, ...],
) -> ICPRequiredInformationAssessment:
    relevant = tuple(fact for fact in facts if fact.key == key)
    confirmed = tuple(
        fact
        for fact in relevant
        if fact.classification is SalesEvidenceClassification.CONFIRMED
    )
    references = _references(confirmed)
    if confirmed:
        if len({_canonical_value(fact.value) for fact in confirmed}) > 1:
            return ICPRequiredInformationAssessment(
                key,
                ICPRequiredInformationStatus.GAP,
                ICPReasonCode.CONFLICTING_EVIDENCE,
                references,
            )
        return ICPRequiredInformationAssessment(
            key,
            ICPRequiredInformationStatus.KNOWN,
            ICPReasonCode.REQUIRED_INFORMATION_CONFIRMED,
            references,
        )
    if any(
        fact.classification is SalesEvidenceClassification.INFERENCE for fact in relevant
    ):
        reason = ICPReasonCode.REQUIRED_INFORMATION_INFERENCE_ONLY
    else:
        reason = ICPReasonCode.REQUIRED_INFORMATION_MISSING
    return ICPRequiredInformationAssessment(
        key,
        ICPRequiredInformationStatus.GAP,
        reason,
    )


def _matches(
    rule: SalesPlaybookICPCriterion | SalesPlaybookICPDisqualifier,
    fact_value: str | float | None,
) -> bool:
    if fact_value is None:
        return False
    if rule.operator is SalesPlaybookCriterionOperator.IN:
        return _canonical_value(fact_value) in {
            _canonical_value(value) for value in rule.values
        }
    expected = rule.values[0]
    if rule.operator is SalesPlaybookCriterionOperator.EQUALS:
        return _canonical_value(fact_value) == _canonical_value(expected)
    fact_number = float(fact_value)
    expected_number = float(expected)
    if rule.operator is SalesPlaybookCriterionOperator.GTE:
        return fact_number >= expected_number
    if rule.operator is SalesPlaybookCriterionOperator.LTE:
        return fact_number <= expected_number
    raise ICPScoringContractError("ICP criterion operator is unsupported")


def _validate_execution_context(
    context: AgentSkillExecutionContext,
    definition: AgentSkillDefinition,
) -> None:
    if (
        context.department is not definition.department
        or context.skill_key != definition.key
        or context.skill_version != definition.version
        or context.employee_role is not AIEmployeeRoleKey.QUALIFICATION
        or context.capability is not BusinessCapabilityKey.QUALIFY_LEAD
        or context.effective_tool_ceiling
        or context.input_contract != definition.input_contract
        or context.output_contract != definition.output_contract
        or context.validator != definition.validator
        or context.instruction_component != definition.instruction_component
        or context.attribution_identifier != definition.attribution_identifier
    ):
        raise ICPScoringAuthorizationError(
            "AgentSkill context is not authorized for icp_scoring:v1"
        )


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _is_non_negative_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _canonical_value(value: str | float | None) -> tuple[str, str]:
    if isinstance(value, str):
        return "text", _normalize_text(value)
    if _is_non_negative_finite_number(value):
        return "number", format(float(value), ".15g")
    raise ICPScoringContractError("ICP fact value is invalid")


def _references(facts: tuple[ICPFact, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                fact.source_reference
                for fact in facts
                if fact.source_reference is not None
            }
        )
    )
