"""Provider-neutral, deterministic configuration and estimation for AI costs.

Rates are expressed in currency units per one million tokens.  The currency is
intentionally deployment-defined; accounting stores only the configured decimal
amount and never performs a pricing network lookup.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from pydantic import BaseModel, ConfigDict, field_validator


TOKENS_PER_PRICING_UNIT = Decimal("1000000")


class AIModelPricingValidationError(ValueError):
    """Raised for invalid provider/model pricing or token input."""


class AIModelPricing(BaseModel):
    """Configured input/output rates for one concrete provider/model pair.

    Both rates use the module's documented one-million-token pricing unit.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    input_cost_per_million_tokens: Decimal
    output_cost_per_million_tokens: Decimal

    @field_validator("provider", "model")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("AI model pricing provider and model must be non-empty")
        return normalized

    @field_validator(
        "input_cost_per_million_tokens",
        "output_cost_per_million_tokens",
        mode="before",
    )
    @classmethod
    def reject_binary_float_rates(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("AI model pricing rates must not use binary float")
        return value

    @field_validator("input_cost_per_million_tokens", "output_cost_per_million_tokens")
    @classmethod
    def validate_rate(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("AI model pricing rates must be non-negative finite decimals")
        return value


@dataclass(frozen=True)
class AICostEstimate:
    """Safe estimate for a concrete invocation; unknown pricing remains explicit."""

    pricing_known: bool
    input_cost: Decimal | None
    output_cost: Decimal | None
    estimated_cost: Decimal | None


class AIModelPricingCatalog:
    """Pure in-memory catalog keyed by provider and model; no network or LLM I/O."""

    def __init__(self, entries: Iterable[AIModelPricing]) -> None:
        self._entries: dict[tuple[str, str], AIModelPricing] = {}
        for entry in entries:
            key = (entry.provider, entry.model)
            if key in self._entries:
                raise AIModelPricingValidationError(
                    "AI model pricing entries must be unique by provider and model"
                )
            self._entries[key] = entry

    def estimate(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> AICostEstimate:
        self._non_negative_tokens(input_tokens, "Input tokens")
        self._non_negative_tokens(output_tokens, "Output tokens")
        key = (self._identifier(provider, "Provider"), self._identifier(model, "Model"))
        pricing = self._entries.get(key)
        if pricing is None:
            return AICostEstimate(
                pricing_known=False,
                input_cost=None,
                output_cost=None,
                estimated_cost=None,
            )

        input_cost = (
            Decimal(input_tokens) * pricing.input_cost_per_million_tokens / TOKENS_PER_PRICING_UNIT
        )
        output_cost = (
            Decimal(output_tokens) * pricing.output_cost_per_million_tokens / TOKENS_PER_PRICING_UNIT
        )
        return AICostEstimate(
            pricing_known=True,
            input_cost=input_cost,
            output_cost=output_cost,
            estimated_cost=input_cost + output_cost,
        )

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not isinstance(value, str):
            raise AIModelPricingValidationError(f"{label} must be a non-empty string")
        normalized = value.strip()
        if not normalized:
            raise AIModelPricingValidationError(f"{label} must be a non-empty string")
        return normalized

    @staticmethod
    def _non_negative_tokens(value: int, label: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise AIModelPricingValidationError(f"{label} must be a non-negative integer")
