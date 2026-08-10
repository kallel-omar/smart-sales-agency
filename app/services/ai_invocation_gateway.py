"""Central, provider-neutral application boundary for real AI invocations.

The gateway composes the existing routing, workspace-limit, tier-resolution,
pricing, and usage-ledger services.  It intentionally stores metadata only;
prompts, completions, and credentials never enter Task 256 usage records.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import UUID

from sqlmodel import Session

from app.models import AIInvocationStatus, Workspace
from app.services.ai_invocation_usage import AIInvocationUsage, AIInvocationUsageService
from app.services.ai_model_routing import (
    AIPremiumJustification,
    AIModelRoutingDecision,
    AIModelRoutingPolicy,
    AIModelRoutingRequest,
    AIModelRoutingTask,
)
from app.services.ai_model_tiers import AIModelSelection, AIModelTier, AIModelTierResolver
from app.services.llm import LLMClient, build_llm
from app.services.workspace_ai_usage_limits import (
    AIWorkspaceUsageLimitDecision,
    AIWorkspaceUsageLimitOutcome,
    AIWorkspaceUsageLimitPolicy,
    AIWorkspaceUsageLimitRequest,
)

if TYPE_CHECKING:
    from app.config import Settings
    from app.models import SalesStage


class AIInvocationBlockedError(RuntimeError):
    """Raised before client construction when workspace policy blocks a call."""

    def __init__(self, decision: AIWorkspaceUsageLimitDecision) -> None:
        self.decision = decision
        super().__init__(decision.explanation)


@dataclass(frozen=True)
class AIInvocationGatewayRequest:
    """Trusted invocation context plus transient prompts for the LLM boundary.

    ``workspace`` is a server-resolved domain entity, never an untrusted ID.
    Optional estimates are supplied only by application code that already has
    them; the gateway never invokes an LLM to derive pre-invocation estimates.
    """

    workspace: Workspace
    task: AIModelRoutingTask | str
    task_identifier: str
    agent_identifier: str
    system_prompt: str
    user_prompt: str
    conversation_id: UUID | None = None
    sales_stage: SalesStage | None = None
    premium_justification: AIPremiumJustification | str | None = None
    expected_total_tokens: int | None = None
    expected_estimated_cost: Decimal | str | int | None = None
    pricing_known: bool | None = None


@dataclass(frozen=True)
class AIInvocationGatewayResult:
    """Safe outcome of one gateway evaluation/invocation."""

    invoked: bool
    content: str | None
    routing_decision: AIModelRoutingDecision
    limit_decision: AIWorkspaceUsageLimitDecision
    selection: AIModelSelection
    usage: AIInvocationUsage | None


class AIInvocationGateway:
    """Run the existing AI policy chain around exactly one LLM completion."""

    def __init__(
        self,
        session: Session,
        settings: "Settings",
        *,
        llm_builder: Callable[..., LLMClient] = build_llm,
    ) -> None:
        self._settings = settings
        self._llm_builder = llm_builder
        self._tier_resolver = AIModelTierResolver.from_settings(settings)
        self._routing_policy = AIModelRoutingPolicy.from_settings(settings)
        self._usage_service = AIInvocationUsageService.from_settings(session, settings)
        self._limit_policy = AIWorkspaceUsageLimitPolicy(
            self._usage_service,
            self._tier_resolver,
        )

    async def invoke(self, request: AIInvocationGatewayRequest) -> AIInvocationGatewayResult:
        routing_decision = self._routing_policy.decide(
            AIModelRoutingRequest(
                task=request.task,
                agent_identifier=request.agent_identifier,
                sales_stage=request.sales_stage,
                premium_justification=request.premium_justification,
            )
        )
        limit_decision = self._limit_policy.evaluate(
            AIWorkspaceUsageLimitRequest(
                workspace=request.workspace,
                requested_tier=routing_decision.tier,
                expected_total_tokens=request.expected_total_tokens,
                expected_estimated_cost=request.expected_estimated_cost,
                pricing_known=request.pricing_known,
            )
        )
        if limit_decision.outcome is AIWorkspaceUsageLimitOutcome.BLOCKED:
            raise AIInvocationBlockedError(limit_decision)

        # A deterministic Task 258 route is an explicit no-op: no resolver
        # mapping, LLM client construction, network request, or usage row.
        if routing_decision.tier is AIModelTier.NONE:
            return AIInvocationGatewayResult(
                invoked=False,
                content=None,
                routing_decision=routing_decision,
                limit_decision=limit_decision,
                selection=self._tier_resolver.resolve(AIModelTier.NONE),
                usage=None,
            )

        final_tier = limit_decision.tier
        if final_tier is None:  # Defensive: blocked decisions returned above.
            raise AIInvocationBlockedError(limit_decision)
        selection = self._tier_resolver.resolve(final_tier)
        assert selection.provider is not None and selection.model is not None

        # Client construction is intentionally after every pure policy gate and
        # after configuration resolution.  A missing final mapping therefore
        # fails before a transport can be created.
        client = self._llm_builder(self._settings, model=selection.model)
        started_at = perf_counter()
        try:
            completion = await client.complete_with_metadata(
                request.system_prompt,
                request.user_prompt,
            )
        except Exception:
            self._record(
                request=request,
                selection=selection,
                input_tokens=0,
                output_tokens=0,
                latency_ms=self._elapsed_ms(started_at),
                status=AIInvocationStatus.FAILED,
            )
            raise

        usage = self._record(
            request=request,
            selection=selection,
            input_tokens=completion.input_tokens or 0,
            output_tokens=completion.output_tokens or 0,
            latency_ms=self._elapsed_ms(started_at),
            status=AIInvocationStatus.SUCCESSFUL,
        )
        return AIInvocationGatewayResult(
            invoked=True,
            content=completion.content,
            routing_decision=routing_decision,
            limit_decision=limit_decision,
            selection=selection,
            usage=usage,
        )

    def _record(
        self,
        *,
        request: AIInvocationGatewayRequest,
        selection: AIModelSelection,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        status: AIInvocationStatus,
    ) -> AIInvocationUsage:
        assert selection.provider is not None and selection.model is not None
        return self._usage_service.record(
            request.workspace,
            task_identifier=request.task_identifier,
            agent_identifier=request.agent_identifier,
            provider=selection.provider,
            model=selection.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status=status,
            conversation_id=request.conversation_id,
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((perf_counter() - started_at) * 1000))
