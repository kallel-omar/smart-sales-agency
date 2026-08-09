"""Provider-neutral AI model tier policy and configuration resolution."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, field_validator

if TYPE_CHECKING:
    from app.config import Settings


class AIModelTier(StrEnum):
    """Business-level capability tiers, independent of any LLM vendor."""

    NONE = "none"
    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"


class AIModelTierMapping(BaseModel):
    """Configured provider/model pair for one LLM-backed tier."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str

    @field_validator("provider", "model")
    @classmethod
    def validate_non_empty_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("AI model tier provider and model must be non-empty")
        return normalized


@dataclass(frozen=True)
class AIModelSelection:
    """Safe resolved selection; ``none`` deliberately has no provider or model."""

    tier: AIModelTier
    provider: str | None = None
    model: str | None = None


class AIModelTierResolutionError(ValueError):
    """Raised when an explicitly requested LLM tier has no configured mapping."""


class AIModelTierResolver:
    """Pure configuration resolver; it never creates a client or calls a provider."""

    def __init__(self, mappings: dict[AIModelTier, AIModelTierMapping]) -> None:
        self._mappings = dict(mappings)

    @classmethod
    def from_settings(cls, settings: "Settings") -> "AIModelTierResolver":
        return cls(settings.ai_model_tier_mappings)

    def resolve(self, tier: AIModelTier | str) -> AIModelSelection:
        try:
            requested_tier = AIModelTier(tier)
        except ValueError as exc:
            raise AIModelTierResolutionError(f"Unknown AI model tier: {tier}") from exc

        if requested_tier is AIModelTier.NONE:
            return AIModelSelection(tier=AIModelTier.NONE)

        mapping = self._mappings.get(requested_tier)
        if mapping is None:
            raise AIModelTierResolutionError(
                f"AI model tier is not configured: {requested_tier.value}"
            )
        return AIModelSelection(
            tier=requested_tier,
            provider=mapping.provider,
            model=mapping.model,
        )
