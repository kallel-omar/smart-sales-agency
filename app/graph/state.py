from typing import Any, TypedDict
from uuid import UUID


class SalesWorkflowState(TypedDict, total=False):
    lead_id: UUID
    lead: Any
    research: dict[str, Any]
    score: int
    qualified: bool
    qualification_reasons: list[str]
    draft_message: str | None
    approval_id: UUID | None
    next_action: str
    status: str
