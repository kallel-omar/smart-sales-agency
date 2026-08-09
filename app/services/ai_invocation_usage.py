"""Provider-neutral, workspace-scoped AI invocation usage persistence."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlmodel import Session, select

from app.models import AIInvocationStatus, AIInvocationUsage, Workspace, utc_now


class AIInvocationUsageValidationError(ValueError):
    """Raised when safe canonical invocation metadata is invalid."""


class AIInvocationUsageService:
    """Persist and read metadata only; prompts, responses, and secrets are excluded."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        workspace: Workspace,
        *,
        task_identifier: str,
        agent_identifier: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        status: AIInvocationStatus,
        conversation_id: UUID | None = None,
        estimated_cost: Decimal | str | int | None = None,
        created_at: datetime | None = None,
    ) -> AIInvocationUsage:
        normalized = {
            "task_identifier": self._identifier(task_identifier, "Task identifier"),
            "agent_identifier": self._identifier(agent_identifier, "Agent identifier"),
            "provider": self._identifier(provider, "Provider"),
            "model": self._identifier(model, "Model"),
        }
        self._non_negative(input_tokens, "Input tokens")
        self._non_negative(output_tokens, "Output tokens")
        self._non_negative(latency_ms, "Latency")
        cost = self._cost(estimated_cost)
        usage = AIInvocationUsage(
            workspace_id=workspace.id,
            conversation_id=conversation_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            latency_ms=latency_ms,
            estimated_cost=cost,
            status=status,
            created_at=created_at or utc_now(),
            **normalized,
        )
        self.session.add(usage)
        self.session.commit()
        self.session.refresh(usage)
        return usage

    def list_for_workspace(self, workspace: Workspace) -> list[AIInvocationUsage]:
        statement = (
            select(AIInvocationUsage)
            .where(AIInvocationUsage.workspace_id == workspace.id)
            .order_by(AIInvocationUsage.created_at.desc(), AIInvocationUsage.id.desc())
        )
        return list(self.session.exec(statement).all())

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
