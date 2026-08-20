from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Lead, Workspace
from app.schemas import ContactRead, CustomerRead, LeadRead
from app.services.customer_contacts import (
    ContactNotFoundError,
    ContactValidationError,
    CustomerContactService,
    CustomerNotFoundError,
    LeadNotFoundError,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = Workspace(slug=slug, name=slug.replace("-", " ").title())
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def _lead(session: Session, workspace: Workspace, suffix: str) -> Lead:
    lead = Lead(
        tenant_id=workspace.slug,
        full_name=f"{suffix} Lead",
        company_name=f"{suffix} Company",
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def test_customer_is_workspace_scoped_and_names_are_not_unique(session: Session) -> None:
    workspace_a = _workspace(session, "customer-a")
    workspace_b = _workspace(session, "customer-b")
    service = CustomerContactService(session)

    customer_a = service.create_customer(workspace_a, name="Acme")
    customer_b = service.create_customer(workspace_b, name="Acme")

    assert customer_a.id != customer_b.id
    assert CustomerRead.model_validate(customer_a).workspace_id == workspace_a.id
    assert [customer.id for customer in service.list_customers(workspace_a)] == [customer_a.id]
    with pytest.raises(CustomerNotFoundError, match="Customer not found"):
        service.get_customer(workspace_b, customer_a.id)


def test_contact_can_be_unaffiliated_or_linked_to_a_customer(session: Session) -> None:
    workspace = _workspace(session, "contact-customer")
    service = CustomerContactService(session)
    customer = service.create_customer(workspace, name="Acme")

    prospect = service.create_contact(workspace, name="  Ada Lovelace  ")
    contact_one = service.create_contact(
        workspace,
        customer_id=customer.id,
        email="ONE@EXAMPLE.TEST ",
    )
    contact_two = service.create_contact(
        workspace,
        customer_id=customer.id,
        phone=" +216 20 000 000 ",
    )

    assert prospect.customer_id is None
    assert prospect.name == "Ada Lovelace"
    assert contact_one.email == "one@example.test"
    assert ContactRead.model_validate(contact_two).phone == "+216 20 000 000"
    assert [contact.id for contact in service.list_contacts(workspace, customer_id=customer.id)] == [
        contact_one.id,
        contact_two.id,
    ]


@pytest.mark.parametrize(
    ("kwargs", "expected_field"),
    [
        ({"name": "Only Name"}, "name"),
        ({"email": "only@example.test"}, "email"),
        ({"phone": "+21620000000"}, "phone"),
    ],
)
def test_contact_requires_one_identity_field(session: Session, kwargs: dict, expected_field: str) -> None:
    workspace = _workspace(session, f"contact-{expected_field}")
    service = CustomerContactService(session)

    contact = service.create_contact(workspace, **kwargs)

    assert getattr(contact, expected_field)
    with pytest.raises(ContactValidationError, match="requires a name, email, or phone"):
        service.create_contact(workspace, name=" ", email="", phone=None)


def test_cross_workspace_customer_and_contact_access_is_rejected(session: Session) -> None:
    workspace_a = _workspace(session, "contact-boundary-a")
    workspace_b = _workspace(session, "contact-boundary-b")
    service = CustomerContactService(session)
    customer_a = service.create_customer(workspace_a, name="Acme")
    contact_a = service.create_contact(workspace_a, name="Ada")

    with pytest.raises(CustomerNotFoundError, match="Customer not found"):
        service.create_contact(workspace_b, customer_id=customer_a.id, name="Grace")
    with pytest.raises(ContactNotFoundError, match="Contact not found"):
        service.get_contact(workspace_b, contact_a.id)


def test_legacy_lead_remains_unlinked_and_same_workspace_contact_can_link(session: Session) -> None:
    workspace = _workspace(session, "lead-contact")
    service = CustomerContactService(session)
    lead_one = _lead(session, workspace, "One")
    lead_two = _lead(session, workspace, "Two")
    contact = service.create_contact(workspace, email="person@example.test")

    assert LeadRead.model_validate(lead_one).contact_id is None
    linked_one = service.link_contact(workspace, lead_one.id, contact.id)
    linked_two = service.link_contact(workspace, lead_two.id, contact.id)

    assert linked_one.contact_id == contact.id
    assert linked_two.contact_id == contact.id
    assert linked_one.full_name == "One Lead"
    assert linked_one.company_name == "One Company"


def test_cross_workspace_contact_cannot_link_to_lead(session: Session) -> None:
    workspace_a = _workspace(session, "lead-boundary-a")
    workspace_b = _workspace(session, "lead-boundary-b")
    service = CustomerContactService(session)
    lead_a = _lead(session, workspace_a, "A")
    contact_b = service.create_contact(workspace_b, name="Contact B")

    with pytest.raises(ContactNotFoundError, match="Contact not found"):
        service.link_contact(workspace_a, lead_a.id, contact_b.id)
    with pytest.raises(LeadNotFoundError, match="Lead not found"):
        service.link_contact(workspace_b, lead_a.id, contact_b.id)
