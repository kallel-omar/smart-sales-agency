"""Small provider-neutral evidence contracts shared by governed Sales skills."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SalesEvidenceClassification(StrEnum):
    CONFIRMED = "confirmed"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class SalesEvidenceSourceType(StrEnum):
    LEAD_RECORD = "lead_record"
    CONVERSATION = "conversation"
    LEAD_RESEARCH = "lead_research"
    MISSING = "missing"


class SalesEvidenceContractError(ValueError):
    """Raised when a Sales skill evidence item is malformed."""


@dataclass(frozen=True, slots=True)
class SalesEvidenceItem:
    classification: SalesEvidenceClassification
    claim: str
    source_type: SalesEvidenceSourceType
    source_reference: str | None = None
    captured_at: str | None = None

    @classmethod
    def from_value(cls, value: object) -> SalesEvidenceItem:
        if not isinstance(value, dict):
            raise SalesEvidenceContractError("Evidence item must be an object")
        required = {
            "classification",
            "claim",
            "source_type",
            "source_reference",
            "captured_at",
        }
        if set(value) != required:
            raise SalesEvidenceContractError("Evidence item fields are invalid")
        try:
            classification = SalesEvidenceClassification(value["classification"])
            source_type = SalesEvidenceSourceType(value["source_type"])
        except (TypeError, ValueError) as exc:
            raise SalesEvidenceContractError("Evidence classification or source is invalid") from exc
        claim = _required_text(value["claim"], "claim", 500)
        source_reference = _optional_text(
            value["source_reference"], "source_reference", 200
        )
        captured_at = _optional_text(value["captured_at"], "captured_at", 100)
        if classification is SalesEvidenceClassification.UNKNOWN:
            if source_type is not SalesEvidenceSourceType.MISSING:
                raise SalesEvidenceContractError("Unknown evidence must use the missing source")
            if source_reference is not None or captured_at is not None:
                raise SalesEvidenceContractError("Unknown evidence cannot claim a source")
        elif source_type is SalesEvidenceSourceType.MISSING or source_reference is None:
            raise SalesEvidenceContractError("Supported evidence requires a source reference")
        return cls(
            classification=classification,
            claim=claim,
            source_type=source_type,
            source_reference=source_reference,
            captured_at=captured_at,
        )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "classification": self.classification.value,
            "claim": self.claim,
            "source_type": self.source_type.value,
            "source_reference": self.source_reference,
            "captured_at": self.captured_at,
        }


def _required_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise SalesEvidenceContractError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise SalesEvidenceContractError(f"{field} is invalid")
    return normalized


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, field, maximum)
