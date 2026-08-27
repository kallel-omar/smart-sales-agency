"""Typed, workspace-owned Sales Playbook v1 business-policy contract."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

SALES_PLAYBOOK_SCHEMA_VERSION = 1
MAX_SALES_PLAYBOOK_BYTES = 32_000
MAX_ICP_CRITERIA = 25
MAX_ICP_DISQUALIFIERS = 20
MAX_QUALIFICATION_REQUIRED_INFORMATION = 20
MAX_CRITERION_VALUES = 20
MAX_CRITERION_VALUE_LENGTH = 200
MAX_REQUIRED_INFORMATION_DESCRIPTION_LENGTH = 500

_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SalesPlaybookCriterionType(StrEnum):
    INDUSTRY = "industry"
    COUNTRY = "country"
    CUSTOMER_TYPE = "customer_type"
    USE_CASE = "use_case"
    BUSINESS_PROBLEM = "business_problem"
    COMPANY_SIZE = "company_size"
    CHANNEL_VOLUME = "channel_volume"


class SalesPlaybookCriterionOperator(StrEnum):
    EQUALS = "equals"
    IN = "in"
    GTE = "gte"
    LTE = "lte"


class SalesPlaybookCriterionImportance(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"


class SalesPlaybookCriterionValueKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"


class SalesPlaybookCriterionSpecification(BaseModel):
    """Server-owned value/operator policy for one criterion type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value_kind: SalesPlaybookCriterionValueKind
    operators: frozenset[SalesPlaybookCriterionOperator]


_TEXT_OPERATORS = frozenset(
    {
        SalesPlaybookCriterionOperator.EQUALS,
        SalesPlaybookCriterionOperator.IN,
    }
)
_NUMBER_OPERATORS = frozenset(
    {
        SalesPlaybookCriterionOperator.EQUALS,
        SalesPlaybookCriterionOperator.GTE,
        SalesPlaybookCriterionOperator.LTE,
    }
)

SALES_PLAYBOOK_CRITERION_REGISTRY: dict[
    SalesPlaybookCriterionType, SalesPlaybookCriterionSpecification
] = {
    criterion_type: SalesPlaybookCriterionSpecification(
        value_kind=SalesPlaybookCriterionValueKind.TEXT,
        operators=_TEXT_OPERATORS,
    )
    for criterion_type in (
        SalesPlaybookCriterionType.INDUSTRY,
        SalesPlaybookCriterionType.COUNTRY,
        SalesPlaybookCriterionType.CUSTOMER_TYPE,
        SalesPlaybookCriterionType.USE_CASE,
        SalesPlaybookCriterionType.BUSINESS_PROBLEM,
    )
} | {
    criterion_type: SalesPlaybookCriterionSpecification(
        value_kind=SalesPlaybookCriterionValueKind.NUMBER,
        operators=_NUMBER_OPERATORS,
    )
    for criterion_type in (
        SalesPlaybookCriterionType.COMPANY_SIZE,
        SalesPlaybookCriterionType.CHANNEL_VOLUME,
    )
}

SalesPlaybookCriterionValue = StrictStr | StrictInt | StrictFloat


class _SalesPlaybookRuleBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: Annotated[str, Field(min_length=1, max_length=64)]
    criterion_type: SalesPlaybookCriterionType
    operator: SalesPlaybookCriterionOperator
    values: Annotated[
        tuple[SalesPlaybookCriterionValue, ...],
        Field(min_length=1, max_length=MAX_CRITERION_VALUES),
    ]

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if _SAFE_KEY.fullmatch(value) is None:
            raise ValueError("Playbook keys must use lowercase letters, numbers, and underscores")
        return value

    @field_validator("values", mode="before")
    @classmethod
    def normalize_values(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[object] = []
        for item in value:
            if isinstance(item, str):
                text = " ".join(unicodedata.normalize("NFKC", item).split()).casefold()
                if not text or len(text) > MAX_CRITERION_VALUE_LENGTH:
                    raise ValueError("Categorical Playbook values must be bounded non-empty text")
                normalized.append(text)
            else:
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def validate_registry_contract(self):
        specification = SALES_PLAYBOOK_CRITERION_REGISTRY[self.criterion_type]
        if self.operator not in specification.operators:
            raise ValueError("Criterion operator is not supported for this criterion type")
        if self.operator is not SalesPlaybookCriterionOperator.IN and len(self.values) != 1:
            raise ValueError("This criterion operator requires exactly one value")

        if specification.value_kind is SalesPlaybookCriterionValueKind.TEXT:
            if not all(isinstance(value, str) for value in self.values):
                raise ValueError("Categorical criteria require text values")
        elif not all(_is_non_negative_finite_number(value) for value in self.values):
            raise ValueError("Numeric criteria require finite non-negative numeric values")

        normalized_values = tuple(_canonical_value(value) for value in self.values)
        if len(set(normalized_values)) != len(normalized_values):
            raise ValueError("Criterion values must be unique after normalization")
        return self


class SalesPlaybookICPCriterion(_SalesPlaybookRuleBase):
    importance: SalesPlaybookCriterionImportance


class SalesPlaybookICPDisqualifier(_SalesPlaybookRuleBase):
    pass


class SalesPlaybookRequiredInformation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[
        str,
        Field(min_length=1, max_length=MAX_REQUIRED_INFORMATION_DESCRIPTION_LENGTH),
    ]

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if _SAFE_KEY.fullmatch(value) is None:
            raise ValueError("Playbook keys must use lowercase letters, numbers, and underscores")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized:
            raise ValueError("Required-information description cannot be blank")
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("Required-information description contains control characters")
        return normalized


class SalesPlaybookICP(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    criteria: Annotated[
        tuple[SalesPlaybookICPCriterion, ...],
        Field(max_length=MAX_ICP_CRITERIA),
    ] = ()
    disqualifiers: Annotated[
        tuple[SalesPlaybookICPDisqualifier, ...],
        Field(max_length=MAX_ICP_DISQUALIFIERS),
    ] = ()

    @model_validator(mode="after")
    def validate_unique_rules(self):
        criteria_keys = [item.key for item in self.criteria]
        disqualifier_keys = [item.key for item in self.disqualifiers]
        all_keys = criteria_keys + disqualifier_keys
        if len(set(all_keys)) != len(all_keys):
            raise ValueError("ICP criterion and disqualifier keys must be unique")

        criterion_signatures = [_rule_signature(item) for item in self.criteria]
        disqualifier_signatures = [_rule_signature(item) for item in self.disqualifiers]
        if len(set(criterion_signatures)) != len(criterion_signatures):
            raise ValueError("Equivalent ICP criteria cannot be duplicated")
        if len(set(disqualifier_signatures)) != len(disqualifier_signatures):
            raise ValueError("Equivalent ICP disqualifiers cannot be duplicated")
        if set(criterion_signatures) & set(disqualifier_signatures):
            raise ValueError("The same rule cannot be both an ICP criterion and disqualifier")
        return self


class SalesPlaybookQualification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_information: Annotated[
        tuple[SalesPlaybookRequiredInformation, ...],
        Field(max_length=MAX_QUALIFICATION_REQUIRED_INFORMATION),
    ] = ()

    @model_validator(mode="after")
    def validate_unique_keys(self):
        keys = [item.key for item in self.required_information]
        if len(set(keys)) != len(keys):
            raise ValueError("Qualification required-information keys must be unique")
        return self


class SalesPlaybookV1(BaseModel):
    """Immutable v1 policy; prospect and execution state are deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SALES_PLAYBOOK_SCHEMA_VERSION]
    icp: SalesPlaybookICP
    qualification: SalesPlaybookQualification

    @model_validator(mode="after")
    def validate_total_size(self):
        serialized = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized) > MAX_SALES_PLAYBOOK_BYTES:
            raise ValueError("Sales Playbook exceeds the maximum serialized size")
        return self


def _is_non_negative_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _canonical_value(value: SalesPlaybookCriterionValue) -> tuple[str, str]:
    if isinstance(value, str):
        return "text", value
    return "number", format(float(value), ".15g")


def _rule_signature(
    rule: SalesPlaybookICPCriterion | SalesPlaybookICPDisqualifier,
) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    return (
        rule.criterion_type.value,
        rule.operator.value,
        tuple(_canonical_value(value) for value in rule.values),
    )
