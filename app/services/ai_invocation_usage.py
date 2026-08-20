"""Provider-neutral, workspace-scoped AI invocation usage persistence."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from uuid import UUID

from sqlmodel import Session, select

from app.core.ai_execution_attribution import AIExecutionAttribution
from app.models import AIInvocationStatus, AIInvocationUsage, Workspace, utc_now
from app.services.ai_execution_attribution import AIExecutionAttributionService
from app.services.ai_model_pricing import AIModelPricingCatalog

if TYPE_CHECKING:
    from app.config import Settings


class AIInvocationUsageValidationError(ValueError):
    """Raised when safe canonical invocation metadata is invalid."""


@dataclass(frozen=True)
class AIInvocationUsageSummary:
    """Workspace-only aggregate that keeps unknown pricing visible."""

    invocation_count: int
    successful_invocation_count: int
    failed_invocation_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    unknown_token_usage_invocation_count: int
    known_estimated_spend: Decimal
    unknown_pricing_invocation_count: int


class AIInvocationUsageService:
    """Persist and read metadata only; prompts, responses, and secrets are excluded."""

    def __init__(
        self,
        session: Session,
        *,
        pricing_catalog: AIModelPricingCatalog | None = None,
    ) -> None:
        self.session = session
        self._pricing_catalog = pricing_catalog
        self._attribution_service = AIExecutionAttributionService(session)

    @classmethod
    def from_settings(cls, session: Session, settings: "Settings") -> "AIInvocationUsageService":
        return cls(session, pricing_catalog=AIModelPricingCatalog(settings.ai_model_pricing))

    def record(
        self,
        workspace: Workspace,
        *,
        task_identifier: str,
        agent_identifier: str,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
        status: AIInvocationStatus,
        conversation_id: UUID | None = None,
        total_tokens: int | None = None,
        estimated_cost: Decimal | str | int | None = None,
        created_at: datetime | None = None,
        attribution: AIExecutionAttribution | None = None,
    ) -> AIInvocationUsage:
        attribution = self.validate_attribution(workspace, attribution)
        normalized = {
            "task_identifier": self._identifier(task_identifier, "Task identifier"),
            "agent_identifier": self._identifier(agent_identifier, "Agent identifier"),
            "provider": self._identifier(provider, "Provider"),
            "model": self._identifier(model, "Model"),
        }
        input_tokens = self._optional_non_negative(input_tokens, "Input tokens")
        output_tokens = self._optional_non_negative(output_tokens, "Output tokens")
        total_tokens = self._canonical_total_tokens(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
        self._non_negative(latency_ms, "Latency")
        cost = self._estimated_cost(
            provider=normalized["provider"],
            model=normalized["model"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
        )
        usage = AIInvocationUsage(
            workspace_id=workspace.id,
            department_id=(attribution.department_id if attribution else None),
            ai_employee_id=(attribution.ai_employee_id if attribution else None),
            capability_id=(attribution.capability_id if attribution else None),
            work_item_id=(attribution.work_item_id if attribution else None),
            conversation_id=conversation_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            estimated_cost=cost,
            status=status,
            created_at=created_at or utc_now(),
            **normalized,
        )
        self.session.add(usage)
        try:
            self.session.commit()
            self.session.refresh(usage)
        except Exception:
            # Keep the caller's session usable after a failed accounting write.
            # The gateway decides which application-level error is safe to show.
            self.session.rollback()
            raise
        return usage

    def validate_attribution(
        self,
        workspace: Workspace,
        attribution: AIExecutionAttribution | None,
    ) -> AIExecutionAttribution | None:
        return self._attribution_service.validate(workspace, attribution)

    def list_for_workspace(self, workspace: Workspace) -> list[AIInvocationUsage]:
        statement = (
            select(AIInvocationUsage)
            .where(AIInvocationUsage.workspace_id == workspace.id)
            .order_by(AIInvocationUsage.created_at.desc(), AIInvocationUsage.id.desc())
        )
        return list(self.session.exec(statement).all())

    def summarize_for_workspace(self, workspace: Workspace) -> AIInvocationUsageSummary:
        """Return deterministic aggregates for the requested workspace only."""

        usages = self.list_for_workspace(workspace)
        known_costs = [usage.estimated_cost for usage in usages if usage.estimated_cost is not None]
        return AIInvocationUsageSummary(
            invocation_count=len(usages),
            successful_invocation_count=sum(
                usage.status is AIInvocationStatus.SUCCESSFUL for usage in usages
            ),
            failed_invocation_count=sum(
                usage.status is AIInvocationStatus.FAILED for usage in usages
            ),
            input_tokens=sum(usage.input_tokens or 0 for usage in usages),
            output_tokens=sum(usage.output_tokens or 0 for usage in usages),
            total_tokens=sum(usage.total_tokens or 0 for usage in usages),
            unknown_token_usage_invocation_count=sum(
                usage.total_tokens is None for usage in usages
            ),
            known_estimated_spend=sum(known_costs, Decimal("0")),
            unknown_pricing_invocation_count=sum(
                usage.estimated_cost is None for usage in usages
            ),
        )

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise AIInvocationUsageValidationError(f"{label} must be between 1 and 200 characters")
        return normalized

    @staticmethod
    def _non_negative(value: int, label: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise AIInvocationUsageValidationError(f"{label} must be a non-negative integer")

    @classmethod
    def _optional_non_negative(cls, value: int | None, label: str) -> int | None:
        if value is None:
            return None
        cls._non_negative(value, label)
        return value

    @classmethod
    def _canonical_total_tokens(
        cls,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
    ) -> int | None:
        total_tokens = cls._optional_non_negative(total_tokens, "Total tokens")
        if input_tokens is None or output_tokens is None:
            return total_tokens
        calculated_total = input_tokens + output_tokens
        if total_tokens is not None and total_tokens != calculated_total:
            raise AIInvocationUsageValidationError(
                "Total tokens must equal input tokens plus output tokens"
            )
        return calculated_total

    @staticmethod
    def _cost(value: Decimal | str | int | None) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise AIInvocationUsageValidationError("Estimated cost must not use binary float")
        try:
            cost = Decimal(value)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AIInvocationUsageValidationError("Estimated cost must be decimal-safe") from exc
        if not cost.is_finite() or cost < 0:
            raise AIInvocationUsageValidationError("Estimated cost must be a non-negative finite decimal")
        return cost

    def _estimated_cost(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        estimated_cost: Decimal | str | int | None,
    ) -> Decimal | None:
        # A configured price cannot produce a safe actual cost without both
        # provider-reported token components.
        if input_tokens is None or output_tokens is None:
            return None
        if self._pricing_catalog is None:
            return self._cost(estimated_cost)
        if estimated_cost is not None:
            raise AIInvocationUsageValidationError(
                "Estimated cost is calculated from configured pricing when a pricing catalog is used"
            )
        return self._pricing_catalog.estimate(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ).estimated_cost
