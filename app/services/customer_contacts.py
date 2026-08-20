from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from app.models import Contact, Customer, Lead, Workspace, utc_now
from app.services.workspaces import WorkspaceNotFoundError


class CustomerNotFoundError(LookupError):
    """Raised when a Customer is absent from the requested workspace."""


class ContactNotFoundError(LookupError):
    """Raised when a Contact is absent from the requested workspace."""


class LeadNotFoundError(LookupError):
    """Raised when a Lead is absent from the requested workspace."""


class CustomerWorkspaceMismatchError(PermissionError):
    """Raised when a Customer does not belong to the requested workspace."""


class ContactValidationError(ValueError):
    """Raised when a Contact has no useful identity fields."""


class CustomerContactRepository:
    """Workspace-scoped persistence queries for shared business identity."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_customer(self, workspace: Workspace, customer_id: UUID) -> Customer:
        customer = self.session.exec(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.workspace_id == workspace.id,
            )
        ).first()
        if customer is None:
            raise CustomerNotFoundError("Customer not found")
        return customer

    def list_customers(self, workspace: Workspace) -> list[Customer]:
        return list(
            self.session.exec(
                select(Customer)
                .where(Customer.workspace_id == workspace.id)
                .order_by(Customer.created_at.asc(), Customer.id.asc())
            ).all()
        )

    def get_contact(self, workspace: Workspace, contact_id: UUID) -> Contact:
        contact = self.session.exec(
            select(Contact).where(
                Contact.id == contact_id,
                Contact.workspace_id == workspace.id,
            )
        ).first()
        if contact is None:
            raise ContactNotFoundError("Contact not found")
        return contact

    def list_contacts(
        self,
        workspace: Workspace,
        customer_id: UUID | None = None,
    ) -> list[Contact]:
        statement = select(Contact).where(Contact.workspace_id == workspace.id)
        if customer_id is not None:
            statement = statement.where(Contact.customer_id == customer_id)
        statement = statement.order_by(Contact.created_at.asc(), Contact.id.asc())
        return list(self.session.exec(statement).all())

    def save(self, record: Customer | Contact | Lead) -> Customer | Contact | Lead:
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record


class CustomerContactService:
    """Minimal shared Customer, Contact, and Lead-contact persistence boundary."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = CustomerContactRepository(session)

    def create_customer(self, workspace: Workspace, *, name: str) -> Customer:
        self._require_workspace(workspace)
        return self.repository.save(
            Customer(workspace_id=workspace.id, name=self._required_text(name, "Customer name", 200))
        )  # type: ignore[return-value]

    def get_customer(self, workspace: Workspace, customer_id: UUID) -> Customer:
        self._require_workspace(workspace)
        return self.repository.get_customer(workspace, customer_id)

    def list_customers(self, workspace: Workspace) -> list[Customer]:
        self._require_workspace(workspace)
        return self.repository.list_customers(workspace)

    def create_contact(
        self,
        workspace: Workspace,
        *,
        customer_id: UUID | None = None,
        name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> Contact:
        self._require_workspace(workspace)
        normalized_name = self._optional_text(name, 200)
        normalized_email = self._optional_text(email, 320)
        normalized_phone = self._optional_text(phone, 50)
        if normalized_email is not None:
            normalized_email = normalized_email.lower()
        if not any((normalized_name, normalized_email, normalized_phone)):
            raise ContactValidationError("Contact requires a name, email, or phone")
        if customer_id is not None:
            self.repository.get_customer(workspace, customer_id)
        return self.repository.save(
            Contact(
                workspace_id=workspace.id,
                customer_id=customer_id,
                name=normalized_name,
                email=normalized_email,
                phone=normalized_phone,
            )
        )  # type: ignore[return-value]

    def get_contact(self, workspace: Workspace, contact_id: UUID) -> Contact:
        self._require_workspace(workspace)
        return self.repository.get_contact(workspace, contact_id)

    def list_contacts(
        self,
        workspace: Workspace,
        *,
        customer_id: UUID | None = None,
    ) -> list[Contact]:
        self._require_workspace(workspace)
        if customer_id is not None:
            self.repository.get_customer(workspace, customer_id)
        return self.repository.list_contacts(workspace, customer_id)

    def link_contact(
        self,
        workspace: Workspace,
        lead_id: UUID,
        contact_id: UUID,
    ) -> Lead:
        self._require_workspace(workspace)
        lead = self.session.exec(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == workspace.slug)
        ).first()
        if lead is None:
            raise LeadNotFoundError("Lead not found")
        contact = self.repository.get_contact(workspace, contact_id)
        if contact.workspace_id != workspace.id:
            raise CustomerWorkspaceMismatchError("Contact does not belong to this workspace")
        lead.contact_id = contact.id
        lead.updated_at = utc_now()
        return self.repository.save(lead)  # type: ignore[return-value]

    def _require_workspace(self, workspace: Workspace) -> None:
        if self.session.get(Workspace, workspace.id) is None:
            raise WorkspaceNotFoundError("Workspace not found")

    @staticmethod
    def _required_text(value: str, label: str, max_length: int) -> str:
        normalized = CustomerContactService._optional_text(value, max_length)
        if normalized is None:
            raise ValueError(f"{label} is required")
        return normalized

    @staticmethod
    def _optional_text(value: str | None, max_length: int) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > max_length:
            raise ValueError("Text value is too long")
        return normalized
