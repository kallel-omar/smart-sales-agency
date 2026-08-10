"""Deterministic, workspace-scoped policy for canonical Sales stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.models import Lead, SalesStage, SalesStageTransitionReasonCode, Workspace
from app.services.repository import NotFoundError, SalesRepository


@dataclass(frozen=True, slots=True)
class SalesStageTransitionInput:
    """Trusted application input for a requested canonical stage transition.

    Workspace authority is injected into the service, never accepted here.
    """

    lead_id: UUID
    requested_stage: SalesStage


@dataclass(frozen=True, slots=True)
class SalesStageTransitionResult:
    """Safe deterministic outcome; no model or prompt data is exposed."""

    allowed: bool
    current_stage: SalesStage
    requested_stage: SalesStage
    resulting_stage: SalesStage
    reason_code: SalesStageTransitionReasonCode
    explanation: str


class SalesStageTransitionService:
    """Evaluate and apply the small, explicit Sales-stage transition matrix."""

    _ALLOWED_NEXT_STAGES: ClassVar[dict[SalesStage, frozenset[SalesStage]]] = {
        SalesStage.INTRODUCTION: frozenset({SalesStage.DISCOVERY}),
        SalesStage.DISCOVERY: frozenset({SalesStage.QUALIFICATION}),
        SalesStage.QUALIFICATION: frozenset({SalesStage.VALUE_PROPOSITION}),
        SalesStage.VALUE_PROPOSITION: frozenset(
            {SalesStage.OBJECTION_HANDLING, SalesStage.CLOSING}
        ),
        SalesStage.OBJECTION_HANDLING: frozenset(
            {SalesStage.VALUE_PROPOSITION, SalesStage.CLOSING, SalesStage.FOLLOW_UP}
        ),
        SalesStage.CLOSING: frozenset({SalesStage.FOLLOW_UP}),
        SalesStage.FOLLOW_UP: frozenset({SalesStage.DISCOVERY}),
    }

    def __init__(self, *, repository: SalesRepository, workspace: Workspace) -> None:
        self.repository = repository
        self.workspace = workspace

    def evaluate(
        self,
        *,
        current_stage: SalesStage,
        requested_stage: SalesStage,
    ) -> SalesStageTransitionResult:
        if requested_stage == current_stage:
            return SalesStageTransitionResult(
                allowed=True,
                current_stage=current_stage,
                requested_stage=requested_stage,
                resulting_stage=current_stage,
                reason_code=SalesStageTransitionReasonCode.SELF_TRANSITION,
                explanation="The lead is already at the requested Sales stage.",
            )

        if requested_stage in self._ALLOWED_NEXT_STAGES[current_stage]:
            return SalesStageTransitionResult(
                allowed=True,
                current_stage=current_stage,
                requested_stage=requested_stage,
                resulting_stage=requested_stage,
                reason_code=SalesStageTransitionReasonCode.TRANSITION_ALLOWED,
                explanation="The requested Sales stage transition is allowed.",
            )

        return SalesStageTransitionResult(
            allowed=False,
            current_stage=current_stage,
            requested_stage=requested_stage,
            resulting_stage=current_stage,
            reason_code=SalesStageTransitionReasonCode.TRANSITION_NOT_ALLOWED,
            explanation="The requested Sales stage transition is not allowed from the current stage.",
        )

    def transition(
        self,
        source: SalesStageTransitionInput,
    ) -> SalesStageTransitionResult:
        lead = self.repository.get_lead(source.lead_id)
        if lead.tenant_id != self.workspace.slug:
            raise NotFoundError("Lead not found")

        result = self.evaluate(
            current_stage=lead.sales_stage,
            requested_stage=source.requested_stage,
        )
        if result.allowed and result.reason_code != SalesStageTransitionReasonCode.SELF_TRANSITION:
            self.repository.update_sales_stage(lead, result.resulting_stage)
        return result

    def canonical_stage_for(self, lead: Lead) -> SalesStage:
        """Return the server-owned canonical stage after workspace validation."""

        if lead.tenant_id != self.workspace.slug:
            raise NotFoundError("Lead not found")
        return lead.sales_stage
