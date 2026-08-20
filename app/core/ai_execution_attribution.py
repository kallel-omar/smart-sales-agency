from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AIExecutionAttribution:
    """Optional HIRI domain attribution for one provider-independent AI call."""

    department_id: UUID | None = None
    ai_employee_id: UUID | None = None
    capability_id: UUID | None = None
    work_item_id: UUID | None = None
