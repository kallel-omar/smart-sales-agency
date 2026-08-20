from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LeadCaptureSignal:
    source: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company_name: str | None = None
    message: str | None = None
    external_reference: str | None = None
    metadata: dict[str, Any] | None = None
    customer_id: UUID | None = None
    contact_id: UUID | None = None
    lead_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class LeadCaptureResult:
    customer_id: UUID | None
    contact_id: UUID
    lead_id: UUID
    work_item_id: UUID
    customer_created: bool
    contact_created: bool
    lead_created: bool
