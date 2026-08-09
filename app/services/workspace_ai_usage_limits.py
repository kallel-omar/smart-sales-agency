"""Deterministic, workspace-owned pre-invocation AI usage limit policy.

The policy consumes Task 256/259's existing usage ledger and never creates an
LLM client, performs network I/O, or owns provider pricing. Callers must pass
a trusted ``Workspace`` already resolved by server-side application context.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.models import Workspace
from app.services.ai_invocation_usage import AIInvocationUsageService
from app.services.ai_model_tiers import (
    AIModelTier,
    AIModelTierResolutionError,
    AIModelTierResolver,
)


class AIWorkspaceUsageLimitOutcome(StrEnum):
    ALLOWED = "allowed"
    DOWNGRADED = "downgraded"
    BLOCKED = "blocked"


class AIWorkspaceUsageLimitReasonCode(StrEnum):
    WITHIN_LIMITS = "within_limits"
    INVOCATION_LIMIT_REACHED = "invocation_limit_reached"
    TOKEN_LIMIT_REACHED = "token_limit_reached"
    TOKEN_ESTIMATE_REQUIRED = "token_estimate_required"
    SPEND_LIMIT_REACHED = "spend_limit_reached"
    UNKNOWN_PRICING_WITH_SPEND_LIMIT = "unknown_pricing_with_spend_limit"
    REQUESTED_TIER_NOT_PERMITTED = "requested_tier_not_permitted"
    DOWNGRADED_FOR_WORKSPACE_POLICY = "downgraded_for_workspace_policy"
    DOWNGRADE_TARGET_UNAVAILABLE = "downgrade_target_unavailable"


class AIWorkspaceUsageLimitConfigurationError(ValueError):
    """Raised when persisted workspace policy values are malformed."""


class AIWorkspaceUsageLimitRequestError(ValueError):
    """Raised when a caller supplies non-canonical pre-invocation estimates."""


@dataclass(frozen=True)
class AIWorkspaceUsageLimitRequest:
    """Trusted workspace context plus optional, pre-invocation usage estimates.

    ``expected_total_tokens`` and ``expected_estimated_cost`` are estimates
    supplied by server-side application code where available. They are never
    inferred from an LLM call. A spend cap requires explicitly known pricing.
    """

    workspace: Workspace
    requested_tier: AIModelTier | str
    expected_total_tokens: int | None = None
    expected_estimated_cost: Decimal | str | int | None = None
    pricing_known: bool | None = None


@dataclass(frozen=True)
class AIWorkspaceUsageLimitDecision:
    outcome: AIWorkspaceUsageLimitOutcome
    requested_tier: AIModelTier
    tier: AIModelTier | None
    reason_code: AIWorkspaceUsageLimitReasonCode
    explanation: str


@dataclass(frozen=True)
class _WorkspaceAIUsagePolicyConfiguration:
    invocation_limit: int | None
    total_token_limit: int | None
    estimated_spend_limit: Decimal | None
    permitted_tiers: frozenset[AIModelTier]
    downgrade_mappings: dict[AIModelTier, AIModelTier]


class AIWorkspaceUsageLimitPolicy:
    """Pure policy over persisted usage and trusted workspace configuration."""

    _TIER_RANK = {
        AIModelTier.NONE: 0,
        AIModelTier.ECONOMY: 1,
        AIModelTier.STANDARD: 2,
        AIModelTier.PREMIUM: 3,
    }

    def __init__(
        self,
        usage_service: AIInvocationUsageService,
        tier_resolver: AIModelTierResolver,
    ) -> None:
        self._usage_service = usage_service
        self._tier_resolver = tier_resolver

    def evaluate(
        self,
        request: AIWorkspaceUsageLimitRequest,
    ) -> AIWorkspaceUsageLimitDecision:
        requested_tier = self._tier(request.requested_tier)
        configuration = self._configuration(request.workspace)
        expected_tokens = self._expected_tokens(request.expected_total_tokens)
        expected_cost = self._expected_cost(request.expected_estimated_cost)
        self._validate_pricing_estimate(request.pricing_known, expected_cost)

        # ``none`` represents no LLM invocation and therefore consumes no AI
        # invocation/token/spend budget.
        if requested_tier is AIModelTier.NONE:
            return self._allowed(requested_tier)

        summary = self._usage_service.summarize_for_workspace(request.workspace)
        hard_limit_decision = self._hard_limit_decision(
            requested_tier=requested_tier,
            configuration=configuration,
            invocation_count=summary.invocation_count,
            total_tokens=summary.total_tokens,
            known_estimated_spend=summary.known_estimated_spend,
            unknown_pricing_invocation_count=summary.unknown_pricing_invocation_count,
            expected_tokens=expected_tokens,
            expected_cost=expected_cost,
            pricing_known=request.pricing_known,
        )
        if hard_limit_decision is not None:
            return hard_limit_decision

        if requested_tier in configuration.permitted_tiers:
            return self._allowed(requested_tier)

        target_tier = configuration.downgrade_mappings.get(requested_tier)
        if target_tier is None:
            return AIWorkspaceUsageLimitDecision(
                outcome=AIWorkspaceUsageLimitOutcome.BLOCKED,
                requested_tier=requested_tier,
                tier=None,
                reason_code=AIWorkspaceUsageLimitReasonCode.REQUESTED_TIER_NOT_PERMITTED,
                explanation="The requested AI tier is not permitted for this workspace.",
            )
        if target_tier not in configuration.permitted_tiers:
            return self._downgrade_target_unavailable(requested_tier)
        try:
            self._tier_resolver.resolve(target_tier)
        except AIModelTierResolutionError:
            return self._downgrade_target_unavailable(requested_tier)

        return AIWorkspaceUsageLimitDecision(
            outcome=AIWorkspaceUsageLimitOutcome.DOWNGRADED,
            requested_tier=requested_tier,
            tier=target_tier,
            reason_code=AIWorkspaceUsageLimitReasonCode.DOWNGRADED_FOR_WORKSPACE_POLICY,
            explanation="The requested AI tier was downgraded by workspace policy.",
        )

    def _hard_limit_decision(
        self,
        *,
        requested_tier: AIModelTier,
        configuration: _WorkspaceAIUsagePolicyConfiguration,
        invocation_count: int,
        total_tokens: int,
        known_estimated_spend: Decimal,
        unknown_pricing_invocation_count: int,
        expected_tokens: int | None,
        expected_cost: Decimal | None,
        pricing_known: bool | None,
    ) -> AIWorkspaceUsageLimitDecision | None:
        if (
            configuration.invocation_limit is not None
            and invocation_count + 1 > configuration.invocation_limit
        ):
            return self._blocked(
                requested_tier,
                AIWorkspaceUsageLimitReasonCode.INVOCATION_LIMIT_REACHED,
                "The workspace AI invocation limit has been reached.",
            )

        if configuration.total_token_limit is not None:
            if expected_tokens is None:
                return self._blocked(
                    requested_tier,
                    AIWorkspaceUsageLimitReasonCode.TOKEN_ESTIMATE_REQUIRED,
                    "A token estimate is required while a workspace token limit is enforced.",
                )
            if total_tokens + expected_tokens > configuration.total_token_limit:
                return self._blocked(
                    requested_tier,
                    AIWorkspaceUsageLimitReasonCode.TOKEN_LIMIT_REACHED,
                    "The workspace AI token limit would be exceeded.",
                )

        if configuration.estimated_spend_limit is not None:
            if (
                unknown_pricing_invocation_count > 0
                or pricing_known is not True
                or expected_cost is None
            ):
                return self._blocked(
                    requested_tier,
                    AIWorkspaceUsageLimitReasonCode.UNKNOWN_PRICING_WITH_SPEND_LIMIT,
                    "Known pricing is required while a workspace spend limit is enforced.",
                )
            if known_estimated_spend + expected_cost > configuration.estimated_spend_limit:
                return self._blocked(
                    requested_tier,
                    AIWorkspaceUsageLimitReasonCode.SPEND_LIMIT_REACHED,
                    "The workspace AI spend limit would be exceeded.",
                )

        return None

    def _configuration(self, workspace: Workspace) -> _WorkspaceAIUsagePolicyConfiguration:
        permitted_values = workspace.ai_permitted_model_tiers
        if permitted_values is None:
            permitted = frozenset(AIModelTier)
        else:
            parsed = [self._tier(value, configuration=True) for value in permitted_values]
            if len(parsed) != len(set(parsed)):
                raise AIWorkspaceUsageLimitConfigurationError(
                    "Workspace permitted AI tiers must be unique"
                )
            permitted = frozenset(parsed) | {AIModelTier.NONE}

        downgrade_mappings: dict[AIModelTier, AIModelTier] = {}
        for source, target in workspace.ai_model_tier_downgrade_mappings.items():
            source_tier = self._tier(source, configuration=True)
            target_tier = self._tier(target, configuration=True)
            if (
                source_tier is AIModelTier.NONE
                or target_tier is AIModelTier.NONE
                or self._TIER_RANK[target_tier] >= self._TIER_RANK[source_tier]
            ):
                raise AIWorkspaceUsageLimitConfigurationError(
                    "Workspace AI tier downgrades must select a lower LLM tier"
                )
            downgrade_mappings[source_tier] = target_tier

        return _WorkspaceAIUsagePolicyConfiguration(
            invocation_limit=self._limit(workspace.ai_invocation_limit, "AI invocation limit"),
            total_token_limit=self._limit(workspace.ai_total_token_limit, "AI token limit"),
            estimated_spend_limit=self._spend_limit(workspace.ai_estimated_spend_limit),
            permitted_tiers=permitted,
            downgrade_mappings=downgrade_mappings,
        )

    @staticmethod
    def _limit(value: int | None, label: str) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise AIWorkspaceUsageLimitConfigurationError(
                f"{label} must be a non-negative integer"
            )
        return value

    @staticmethod
    def _spend_limit(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise AIWorkspaceUsageLimitConfigurationError(
                "AI spend limit must not use binary float"
            )
        try:
            amount = Decimal(value)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AIWorkspaceUsageLimitConfigurationError(
                "AI spend limit must be decimal-safe"
            ) from exc
        if not amount.is_finite() or amount < 0:
            raise AIWorkspaceUsageLimitConfigurationError(
                "AI spend limit must be a non-negative finite decimal"
            )
        return amount

    @staticmethod
    def _expected_tokens(value: int | None) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise AIWorkspaceUsageLimitRequestError(
                "Expected total tokens must be a non-negative integer"
            )
        return value

    @staticmethod
    def _expected_cost(value: Decimal | str | int | None) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise AIWorkspaceUsageLimitRequestError(
                "Expected estimated cost must not use binary float"
            )
        try:
            amount = Decimal(value)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AIWorkspaceUsageLimitRequestError(
                "Expected estimated cost must be decimal-safe"
            ) from exc
        if not amount.is_finite() or amount < 0:
            raise AIWorkspaceUsageLimitRequestError(
                "Expected estimated cost must be a non-negative finite decimal"
            )
        return amount

    @staticmethod
    def _validate_pricing_estimate(pricing_known: bool | None, expected_cost: Decimal | None) -> None:
        if pricing_known is not None and not isinstance(pricing_known, bool):
            raise AIWorkspaceUsageLimitRequestError("Pricing known must be a boolean")
        if pricing_known is False and expected_cost is not None:
            raise AIWorkspaceUsageLimitRequestError(
                "Unknown pricing cannot include an expected estimated cost"
            )
        if pricing_known is True and expected_cost is None:
            raise AIWorkspaceUsageLimitRequestError(
                "Known pricing requires an expected estimated cost"
            )

    @staticmethod
    def _tier(value: AIModelTier | str, *, configuration: bool = False) -> AIModelTier:
        try:
            return AIModelTier(value)
        except ValueError as exc:
            error = (
                AIWorkspaceUsageLimitConfigurationError
                if configuration
                else AIWorkspaceUsageLimitRequestError
            )
            raise error(f"Unknown AI model tier: {value}") from exc

    @staticmethod
    def _allowed(tier: AIModelTier) -> AIWorkspaceUsageLimitDecision:
        return AIWorkspaceUsageLimitDecision(
            outcome=AIWorkspaceUsageLimitOutcome.ALLOWED,
            requested_tier=tier,
            tier=tier,
            reason_code=AIWorkspaceUsageLimitReasonCode.WITHIN_LIMITS,
            explanation="The requested AI tier is within workspace usage limits.",
        )

    @staticmethod
    def _blocked(
        requested_tier: AIModelTier,
        reason_code: AIWorkspaceUsageLimitReasonCode,
        explanation: str,
    ) -> AIWorkspaceUsageLimitDecision:
        return AIWorkspaceUsageLimitDecision(
            outcome=AIWorkspaceUsageLimitOutcome.BLOCKED,
            requested_tier=requested_tier,
            tier=None,
            reason_code=reason_code,
            explanation=explanation,
        )

    def _downgrade_target_unavailable(
        self,
        requested_tier: AIModelTier,
    ) -> AIWorkspaceUsageLimitDecision:
        return self._blocked(
            requested_tier,
            AIWorkspaceUsageLimitReasonCode.DOWNGRADE_TARGET_UNAVAILABLE,
            "The configured AI tier downgrade target is unavailable.",
        )
