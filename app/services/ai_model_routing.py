"""Deterministic, provider-neutral policy for selecting an AI model tier."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.models import SalesStage
from app.services.ai_model_tiers import (
    AIModelSelection,
    AIModelTier,
    AIModelTierResolver,
)

if TYPE_CHECKING:
    from app.config import Settings


class AIModelRoutingTask(StrEnum):
    """Bounded capability categories supplied by application code before an invocation."""

    DETERMINISTIC = "deterministic"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SIMPLE_SUMMARY = "simple_summary"
    LIGHTWEIGHT_QUALIFICATION = "lightweight_qualification"
    SALES_CONVERSATION = "sales_conversation"
    CONTEXTUAL_CUSTOMER_RESPONSE = "contextual_customer_response"
    COMPLEX_REASONING = "complex_reasoning"
    HIGH_VALUE_CASE = "high_value_case"


class AIPremiumJustification(StrEnum):
    """Explicit, safe policy signals that may permit premium capability."""

    COMPLEX_CASE = "complex_case"
    HIGH_VALUE_CASE = "high_value_case"


class AIModelRoutingReasonCode(StrEnum):
    """Stable, provider-neutral reasons suitable for safe usage metadata."""

    DETERMINISTIC_TASK = "deterministic_task"
    LIGHTWEIGHT_AI_TASK = "lightweight_ai_task"
    STANDARD_CONVERSATION = "standard_conversation"
    EXPLICIT_COMPLEX_CASE = "explicit_complex_case"
    EXPLICIT_HIGH_VALUE_CASE = "explicit_high_value_case"
    PREMIUM_NOT_JUSTIFIED = "premium_not_justified"
    PREMIUM_NOT_PERMITTED = "premium_not_permitted"


@dataclass(frozen=True)
class AIModelRoutingRequest:
    """Provider-neutral input to the deterministic routing policy.

    ``agent_identifier`` follows Task 256's existing safe usage metadata
    convention. ``sales_stage`` reuses the existing sales-domain concept but
    does not affect the default conversation tier today.
    """

    task: AIModelRoutingTask | str
    agent_identifier: str | None = None
    sales_stage: SalesStage | None = None
    premium_justification: AIPremiumJustification | str | None = None


@dataclass(frozen=True)
class AIModelRoutingDecision:
    """Policy result separate from provider/model configuration resolution."""

    tier: AIModelTier
    reason_code: AIModelRoutingReasonCode
    explanation: str


class AIModelRoutingError(ValueError):
    """Raised when a routing request does not use a supported policy value."""


class AIModelRoutingPolicy:
    """Pure routing policy; it never creates an LLM client or performs I/O."""

    _LIGHTWEIGHT_TASKS = frozenset(
        {
            AIModelRoutingTask.CLASSIFICATION,
            AIModelRoutingTask.EXTRACTION,
            AIModelRoutingTask.SIMPLE_SUMMARY,
            AIModelRoutingTask.LIGHTWEIGHT_QUALIFICATION,
        }
    )
    _STANDARD_TASKS = frozenset(
        {
            AIModelRoutingTask.SALES_CONVERSATION,
            AIModelRoutingTask.CONTEXTUAL_CUSTOMER_RESPONSE,
        }
    )

    def __init__(self, *, premium_enabled: bool = False) -> None:
        self._premium_enabled = premium_enabled

    @classmethod
    def from_settings(cls, settings: "Settings") -> "AIModelRoutingPolicy":
        return cls(premium_enabled=settings.ai_model_routing_premium_enabled)

    def decide(self, request: AIModelRoutingRequest) -> AIModelRoutingDecision:
        task = self._task(request.task)

        if task is AIModelRoutingTask.DETERMINISTIC:
            return AIModelRoutingDecision(
                tier=AIModelTier.NONE,
                reason_code=AIModelRoutingReasonCode.DETERMINISTIC_TASK,
                explanation="This operation is deterministic and does not require an LLM.",
            )

        if task in self._LIGHTWEIGHT_TASKS:
            return AIModelRoutingDecision(
                tier=AIModelTier.ECONOMY,
                reason_code=AIModelRoutingReasonCode.LIGHTWEIGHT_AI_TASK,
                explanation="This lightweight AI task is assigned to the economy tier.",
            )

        if task in self._STANDARD_TASKS:
            return AIModelRoutingDecision(
                tier=AIModelTier.STANDARD,
                reason_code=AIModelRoutingReasonCode.STANDARD_CONVERSATION,
                explanation="This contextual customer interaction is assigned to the standard tier.",
            )

        justification = self._justification(request.premium_justification)
        premium_reason = self._premium_reason(task, justification)
        if premium_reason is not None and self._premium_enabled:
            reason_code, explanation = premium_reason
            return AIModelRoutingDecision(
                tier=AIModelTier.PREMIUM,
                reason_code=reason_code,
                explanation=explanation,
            )

        if premium_reason is not None:
            return AIModelRoutingDecision(
                tier=AIModelTier.STANDARD,
                reason_code=AIModelRoutingReasonCode.PREMIUM_NOT_PERMITTED,
                explanation="Premium use is disabled by policy, so the task remains on the standard tier.",
            )

        return AIModelRoutingDecision(
            tier=AIModelTier.STANDARD,
            reason_code=AIModelRoutingReasonCode.PREMIUM_NOT_JUSTIFIED,
            explanation="Premium use was not explicitly justified, so the task remains on the standard tier.",
        )

    @staticmethod
    def resolve(
        decision: AIModelRoutingDecision,
        resolver: AIModelTierResolver,
    ) -> AIModelSelection:
        """Resolve a decision using Task 257's configuration-only resolver."""

        return resolver.resolve(decision.tier)

    @staticmethod
    def _task(value: AIModelRoutingTask | str) -> AIModelRoutingTask:
        try:
            return AIModelRoutingTask(value)
        except ValueError as exc:
            raise AIModelRoutingError(f"Unknown AI model routing task: {value}") from exc

    @staticmethod
    def _justification(
        value: AIPremiumJustification | str | None,
    ) -> AIPremiumJustification | None:
        if value is None:
            return None
        try:
            return AIPremiumJustification(value)
        except ValueError as exc:
            raise AIModelRoutingError(f"Unknown premium justification: {value}") from exc

    @staticmethod
    def _premium_reason(
        task: AIModelRoutingTask,
        justification: AIPremiumJustification | None,
    ) -> tuple[AIModelRoutingReasonCode, str] | None:
        if (
            task is AIModelRoutingTask.COMPLEX_REASONING
            and justification is AIPremiumJustification.COMPLEX_CASE
        ):
            return (
                AIModelRoutingReasonCode.EXPLICIT_COMPLEX_CASE,
                "This explicitly justified complex case is assigned to the premium tier.",
            )
        if (
            task is AIModelRoutingTask.HIGH_VALUE_CASE
            and justification is AIPremiumJustification.HIGH_VALUE_CASE
        ):
            return (
                AIModelRoutingReasonCode.EXPLICIT_HIGH_VALUE_CASE,
                "This explicitly justified high-value case is assigned to the premium tier.",
            )
        return None
