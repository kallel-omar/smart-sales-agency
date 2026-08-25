from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.events import Department as DepartmentKind
from app.core.lead_capture import LeadCaptureResult, LeadCaptureSignal
from app.core.work_items import WorkItemStatus
from app.departments.sales.services.acquisition_coordination import (
    SalesWorkItemResultCoordinator,
)
from app.departments.sales.services.work_item_execution import (
    SalesWorkItemExecutionService,
)
from app.models import Capability, Contact, Customer, Department, Lead, Workspace
from app.services.capabilities import CapabilityService
from app.services.customer_contacts import CustomerContactService, LeadNotFoundError
from app.services.department_supervisors import DepartmentSupervisorRoutingService
from app.services.departments import DepartmentNotFoundError, DepartmentService
from app.services.sales_workforce import SalesWorkforceProvisioningService
from app.services.work_items import WorkItemService
from app.services.workspaces import WorkspaceNotFoundError


class AmbiguousCustomerMatchError(ValueError):
    pass


class AmbiguousContactIdentityError(ValueError):
    pass


class CaptureIdentityConflictError(ValueError):
    pass


class LeadCaptureConfigurationError(ValueError):
    pass


class LeadCaptureInputError(ValueError):
    pass


class LeadCaptureService:
    """Channel-neutral identity capture that creates a queued Sales WorkItem."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.identities = CustomerContactService(session)
        self.departments = DepartmentService(session)
        self.capabilities = CapabilityService(session)
        self.work_items = WorkItemService(session)

    def capture(self, workspace_id: UUID, signal: LeadCaptureSignal) -> LeadCaptureResult:
        workspace = self._workspace(workspace_id)
        self._required_text(signal.source, "Lead source")
        metadata = self._safe_metadata(signal.metadata)
        department, capability = self._capture_configuration(workspace)
        SalesWorkforceProvisioningService(self.session).ensure_default_role(
            workspace,
            department,
            AIEmployeeRoleKey.LEAD_RESEARCH,
        )
        customer, customer_created = self._resolve_customer(workspace, signal)
        contact, contact_created = self._resolve_contact(workspace, signal, customer)
        self._require_customer_contact_consistency(customer, contact)
        lead, lead_created = self._resolve_lead(workspace, signal, contact)
        coordinator = SalesWorkItemResultCoordinator(self.session)
        work_item = coordinator.find_capture_root(workspace, department, lead.id)
        if work_item is None:
            work_item = self.work_items.create_work_item(
                workspace,
                department,
                work_type="lead_capture",
                title="Capture lead",
                capability=capability,
                input=self._work_item_input(
                    signal,
                    customer,
                    contact,
                    lead,
                    metadata,
                    customer_created=customer_created,
                    contact_created=contact_created,
                    lead_created=lead_created,
                ),
            )
        status = WorkItemStatus(work_item.status)
        if status == WorkItemStatus.CREATED:
            decision = DepartmentSupervisorRoutingService(
                self.session
            ).route_and_assign(workspace, work_item.id)
            work_item = self.work_items.get_work_item(workspace, work_item.id)
            if not decision.routable:
                raise LeadCaptureConfigurationError(
                    "No eligible capture_lead AIEmployee assignment is configured"
                )
            status = WorkItemStatus(work_item.status)
        if status == WorkItemStatus.ASSIGNED:
            work_item = SalesWorkItemExecutionService(
                self.session,
                None,
            ).execute_capture(workspace, work_item.id)
            status = WorkItemStatus(work_item.status)
        if status != WorkItemStatus.COMPLETED:
            raise LeadCaptureConfigurationError(
                f"Lead capture WorkItem stopped at {status.value}"
            )
        coordinator.process_completed(workspace, work_item.id)
        return LeadCaptureResult(
            customer_id=customer.id if customer else None,
            contact_id=contact.id,
            lead_id=lead.id,
            work_item_id=work_item.id,
            customer_created=customer_created,
            contact_created=contact_created,
            lead_created=lead_created,
        )

    def _workspace(self, workspace_id: UUID) -> Workspace:
        workspace = self.session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError("Workspace not found")
        return workspace

    def _resolve_customer(
        self, workspace: Workspace, signal: LeadCaptureSignal
    ) -> tuple[Customer | None, bool]:
        if signal.customer_id is not None:
            return self.identities.get_customer(workspace, signal.customer_id), False
        company_name = self._text(signal.company_name)
        if company_name is None:
            return None, False
        matches = [
            customer
            for customer in self.identities.list_customers(workspace)
            if customer.name.casefold() == company_name.casefold()
        ]
        if len(matches) > 1:
            raise AmbiguousCustomerMatchError("Customer name is ambiguous")
        if matches:
            return matches[0], False
        return self.identities.create_customer(workspace, name=company_name), True

    def _resolve_contact(
        self,
        workspace: Workspace,
        signal: LeadCaptureSignal,
        customer: Customer | None,
    ) -> tuple[Contact, bool]:
        if signal.contact_id is not None:
            return self.identities.get_contact(workspace, signal.contact_id), False
        email = self._text(signal.email)
        phone = self._text(signal.phone)
        email_matches = self._contacts_by_email(workspace, email)
        phone_matches = self._contacts_by_phone(workspace, phone)
        candidates = {contact.id: contact for contact in [*email_matches, *phone_matches]}
        if len(candidates) > 1:
            raise AmbiguousContactIdentityError("Contact identity is ambiguous")
        if candidates:
            return next(iter(candidates.values())), False
        return (
            self.identities.create_contact(
                workspace,
                customer_id=customer.id if customer else None,
                name=self._text(signal.name),
                email=email,
                phone=phone,
            ),
            True,
        )

    def _resolve_lead(
        self, workspace: Workspace, signal: LeadCaptureSignal, contact: Contact
    ) -> tuple[Lead, bool]:
        if signal.lead_id is not None:
            lead = self.session.exec(
                select(Lead).where(
                    Lead.id == signal.lead_id, Lead.tenant_id == workspace.slug
                )
            ).first()
            if lead is None:
                raise LeadNotFoundError("Lead not found")
            if lead.contact_id is not None and lead.contact_id != contact.id:
                raise CaptureIdentityConflictError("Lead is linked to a different Contact")
            return self.identities.link_contact(workspace, lead.id, contact.id), False
        name = self._text(signal.name)
        company_name = self._text(signal.company_name)
        lead = Lead(
            tenant_id=workspace.slug,
            full_name=name or "Unknown contact",
            company_name=company_name or "Unknown company",
            email=self._text(signal.email),
            phone=self._text(signal.phone),
            source=self._required_text(signal.source, "Lead source"),
        )
        self.session.add(lead)
        self.session.commit()
        self.session.refresh(lead)
        return self.identities.link_contact(workspace, lead.id, contact.id), True

    def _capture_configuration(
        self, workspace: Workspace
    ) -> tuple[Department, Capability]:
        try:
            department = self.departments.get_for_workspace(
                workspace,
                self._sales_department_id(workspace),
            )
        except DepartmentNotFoundError as exc:
            raise LeadCaptureConfigurationError("Sales Department is not configured") from exc
        capability = self.capabilities.repository.get_by_key(
            workspace, department, BusinessCapabilityKey.CAPTURE_LEAD
        )
        if capability is None:
            raise LeadCaptureConfigurationError("capture_lead Capability is not configured")
        return department, capability

    def _sales_department_id(self, workspace: Workspace) -> UUID:
        for department in self.departments.list_for_workspace(workspace):
            if department.kind == DepartmentKind.SALES:
                return department.id
        raise DepartmentNotFoundError("Department not found")

    def _contacts_by_email(self, workspace: Workspace, email: str | None) -> list[Contact]:
        if email is None:
            return []
        return [
            contact for contact in self.identities.list_contacts(workspace)
            if contact.email == email.lower()
        ]

    def _contacts_by_phone(self, workspace: Workspace, phone: str | None) -> list[Contact]:
        if phone is None:
            return []
        return [contact for contact in self.identities.list_contacts(workspace) if contact.phone == phone]

    @staticmethod
    def _require_customer_contact_consistency(
        customer: Customer | None, contact: Contact
    ) -> None:
        if customer and contact.customer_id and contact.customer_id != customer.id:
            raise CaptureIdentityConflictError("Contact belongs to a different Customer")

    @staticmethod
    def _text(value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @staticmethod
    def _required_text(value: str, label: str) -> str:
        normalized = LeadCaptureService._text(value)
        if normalized is None:
            raise ValueError(f"{label} is required")
        return normalized

    @staticmethod
    def _work_item_input(
        signal: LeadCaptureSignal,
        customer: Customer | None,
        contact: Contact,
        lead: Lead,
        metadata: dict[str, Any] | None,
        *,
        customer_created: bool,
        contact_created: bool,
        lead_created: bool,
    ) -> dict[str, Any]:
        input_data: dict[str, Any] = {
            "lead_id": str(lead.id),
            "contact_id": str(contact.id),
            "source": LeadCaptureService._required_text(signal.source, "Lead source"),
            "customer_created": customer_created,
            "contact_created": contact_created,
            "lead_created": lead_created,
        }
        if customer:
            input_data["customer_id"] = str(customer.id)
        for key, value in (
            ("message", LeadCaptureService._text(signal.message)),
            ("external_reference", LeadCaptureService._text(signal.external_reference)),
            ("metadata", metadata),
        ):
            if value is not None:
                input_data[key] = value
        return input_data

    @staticmethod
    def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        if metadata is None:
            return None
        if not isinstance(metadata, dict):
            raise LeadCaptureInputError("Lead capture metadata must be an object")
        sensitive_keys = {
            "api_key",
            "authorization",
            "credential",
            "password",
            "secret",
            "token",
        }

        def contains_sensitive_key(value: Any) -> bool:
            if isinstance(value, dict):
                return any(
                    str(key).strip().lower() in sensitive_keys
                    or contains_sensitive_key(nested)
                    for key, nested in value.items()
                )
            if isinstance(value, (list, tuple)):
                return any(contains_sensitive_key(item) for item in value)
            return False

        if contains_sensitive_key(metadata):
            raise LeadCaptureInputError("Lead capture metadata cannot contain credentials")
        try:
            json.dumps(metadata)
        except (TypeError, ValueError) as exc:
            raise LeadCaptureInputError("Lead capture metadata must be JSON-safe") from exc
        return dict(metadata)
