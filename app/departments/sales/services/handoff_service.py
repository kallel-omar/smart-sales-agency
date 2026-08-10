"""Application service for deterministic Sales handoff lifecycle transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.models import (
    SalesConversationHandoffStatus,
    SalesHandoffReasonCode,
    Workspace,
)
from app.services.repository import SalesRepository


@dataclass(frozen=True, slots=True)
class SalesHandoffResolutionResult:
    """Safe result for one explicit, server-scoped handoff resolution."""

    lead_id: UUID
    reason_code: SalesHandoffReasonCode
    status: SalesConversationHandoffStatus
    created_at: datetime
    resolved_at: datetime


class SalesConversationHandoffService:
    """Resolve an active handoff without invoking AI or changing approvals."""

    def __init__(self, *, repository: SalesRepository, workspace: Workspace) -> None:
        self.repository = repository
        self.workspace = workspace

    def resolve_active_handoff(self, lead_id: UUID) -> SalesHandoffResolutionResult:
        lead = self.repository.get_lead(lead_id)
        handoff = self.repository.resolve_sales_handoff(
            workspace=self.workspace,
            lead=lead,
        )
        assert handoff.resolved_at is not None
        return SalesHandoffResolutionResult(
            lead_id=lead.id,
            reason_code=handoff.reason_code,
            status=handoff.status,
            created_at=handoff.created_at,
            resolved_at=handoff.resolved_at,
        )
