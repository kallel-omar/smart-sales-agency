from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.capabilities import BusinessCapabilityKey
from app.core.lead_capture import LeadCaptureSignal
from app.core.work_items import WorkItemStatus
from app.models import AIInvocationUsage, Contact, Lead, WorkItem, Workspace
from app.services.capabilities import CapabilityService
from app.services.customer_contacts import (
    ContactNotFoundError,
    CustomerContactService,
    CustomerNotFoundError,
    LeadNotFoundError,
)
from app.services.departments import DepartmentService
from app.services.lead_capture import (
    AmbiguousContactIdentityError,
    AmbiguousCustomerMatchError,
    CaptureIdentityConflictError,
    LeadCaptureConfigurationError,
    LeadCaptureInputError,
    LeadCaptureService,
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


def _workspace(
    session: Session,
    slug: str,
    *,
    department: bool = True,
    capability: bool = True,
) -> Workspace:
    workspace = Workspace(slug=slug, name=slug)
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    if department:
        sales = DepartmentService(session).ensure_sales_department(workspace)
        if capability:
            CapabilityService(session).ensure_for_department(
                workspace, sales, BusinessCapabilityKey.CAPTURE_LEAD
            )
    return workspace


def _lead(session: Session, workspace: Workspace, name: str = "Legacy") -> Lead:
    lead = Lead(
        tenant_id=workspace.slug,
        full_name=name,
        company_name="Legacy Company",
        email="legacy@example.test",
        phone="+216000",
        source="legacy",
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def test_capture_creates_linked_identity_and_unassigned_work_item(session: Session) -> None:
    workspace = _workspace(session, "capture-basic")
    result = LeadCaptureService(session).capture(
        workspace.id,
        LeadCaptureSignal(
            source=" api ",
            name="Ada",
            email="Ada@Example.Test",
            message=" Hello ",
            external_reference=" ref-1 ",
            metadata={"campaign": "x"},
        ),
    )
    contact = session.get(Contact, result.contact_id)
    lead = session.get(Lead, result.lead_id)
    work_item = session.get(WorkItem, result.work_item_id)
    sales = DepartmentService(session).list_for_workspace(workspace)[0]
    capability = CapabilityService(session).repository.get_by_key(
        workspace, sales, BusinessCapabilityKey.CAPTURE_LEAD
    )

    assert result.customer_id is None
    assert result.contact_created and result.lead_created
    assert contact and contact.email == "ada@example.test"
    assert lead and lead.contact_id == contact.id and lead.email == "Ada@Example.Test"
    assert work_item and work_item.status == WorkItemStatus.CREATED
    assert work_item.department_id == sales.id
    assert capability and work_item.capability_id == capability.id
    assert work_item.ai_employee_id is None and work_item.assignment_id is None
    assert work_item.input == {
        "lead_id": str(lead.id),
        "contact_id": str(contact.id),
        "source": "api",
        "message": "Hello",
        "external_reference": "ref-1",
        "metadata": {"campaign": "x"},
    }
    assert session.exec(select(AIInvocationUsage)).all() == []


def test_company_customer_create_reuse_and_ambiguity(session: Session) -> None:
    workspace = _workspace(session, "capture-customer")
    service = LeadCaptureService(session)
    created = service.capture(
        workspace.id,
        LeadCaptureSignal(source="api", name="Ada", company_name=" Acme "),
    )
    reused = service.capture(
        workspace.id,
        LeadCaptureSignal(source="api", name="Grace", company_name="ACME"),
    )
    CustomerContactService(session).create_customer(workspace, name="acme")

    assert created.customer_id is not None and created.customer_created
    assert reused.customer_id == created.customer_id and not reused.customer_created
    with pytest.raises(AmbiguousCustomerMatchError, match="ambiguous"):
        service.capture(
            workspace.id,
            LeadCaptureSignal(source="api", name="Lin", company_name="acme"),
        )


def test_explicit_customer_scope_and_contact_consistency(session: Session) -> None:
    workspace = _workspace(session, "capture-explicit-customer")
    other = _workspace(session, "capture-explicit-customer-other")
    identities = CustomerContactService(session)
    customer_a = identities.create_customer(workspace, name="A")
    customer_b = identities.create_customer(workspace, name="B")
    foreign = identities.create_customer(other, name="Foreign")
    contact = identities.create_contact(
        workspace, customer_id=customer_a.id, name="Ada"
    )

    result = LeadCaptureService(session).capture(
        workspace.id,
        LeadCaptureSignal(source="api", customer_id=customer_a.id, name="Grace"),
    )
    assert result.customer_id == customer_a.id and not result.customer_created
    with pytest.raises(CustomerNotFoundError, match="Customer not found"):
        LeadCaptureService(session).capture(
            workspace.id,
            LeadCaptureSignal(source="api", customer_id=foreign.id, name="Grace"),
        )
    with pytest.raises(CaptureIdentityConflictError, match="different Customer"):
        LeadCaptureService(session).capture(
            workspace.id,
            LeadCaptureSignal(
                source="api", customer_id=customer_b.id, contact_id=contact.id
            ),
        )


def test_email_phone_and_name_matching_rules(session: Session) -> None:
    workspace = _workspace(session, "capture-contact-match")
    other = _workspace(session, "capture-contact-match-other")
    identities = CustomerContactService(session)
    email_contact = identities.create_contact(workspace, email="a@example.test")
    phone_contact = identities.create_contact(workspace, phone="+2161")
    name_contact = identities.create_contact(workspace, name="Ada")
    identities.create_contact(other, email="foreign@example.test")
    service = LeadCaptureService(session)

    by_email = service.capture(
        workspace.id, LeadCaptureSignal(source="api", email=" A@EXAMPLE.TEST ")
    )
    by_phone = service.capture(
        workspace.id, LeadCaptureSignal(source="api", phone=" +2161 ")
    )
    by_name = service.capture(
        workspace.id, LeadCaptureSignal(source="api", name="Ada")
    )
    cross_workspace = service.capture(
        workspace.id,
        LeadCaptureSignal(source="api", email="foreign@example.test"),
    )

    assert by_email.contact_id == email_contact.id and not by_email.contact_created
    assert by_phone.contact_id == phone_contact.id and not by_phone.contact_created
    assert by_name.contact_id != name_contact.id and by_name.contact_created
    assert cross_workspace.contact_created
    with pytest.raises(AmbiguousContactIdentityError, match="ambiguous"):
        service.capture(
            workspace.id,
            LeadCaptureSignal(
                source="api", email="a@example.test", phone="+2161"
            ),
        )


def test_explicit_contact_scope(session: Session) -> None:
    workspace = _workspace(session, "capture-explicit-contact")
    other = _workspace(session, "capture-explicit-contact-other")
    identities = CustomerContactService(session)
    contact = identities.create_contact(workspace, name="Ada")
    foreign = identities.create_contact(other, name="Foreign")

    result = LeadCaptureService(session).capture(
        workspace.id,
        LeadCaptureSignal(source="api", contact_id=contact.id),
    )
    assert result.contact_id == contact.id and not result.contact_created
    with pytest.raises(ContactNotFoundError, match="Contact not found"):
        LeadCaptureService(session).capture(
            workspace.id,
            LeadCaptureSignal(source="api", contact_id=foreign.id),
        )


def test_explicit_lead_reuse_scope_and_identity_conflict(session: Session) -> None:
    workspace = _workspace(session, "capture-explicit-lead")
    other = _workspace(session, "capture-explicit-lead-other")
    identities = CustomerContactService(session)
    contact_a = identities.create_contact(workspace, name="Ada")
    contact_b = identities.create_contact(workspace, name="Grace")
    lead = _lead(session, workspace)
    foreign_lead = _lead(session, other, "Foreign")

    result = LeadCaptureService(session).capture(
        workspace.id,
        LeadCaptureSignal(source="api", lead_id=lead.id, contact_id=contact_a.id),
    )
    assert result.lead_id == lead.id and not result.lead_created
    assert session.get(Lead, lead.id).contact_id == contact_a.id  # type: ignore[union-attr]
    assert session.get(Lead, lead.id).source == "legacy"  # type: ignore[union-attr]
    with pytest.raises(CaptureIdentityConflictError, match="different Contact"):
        LeadCaptureService(session).capture(
            workspace.id,
            LeadCaptureSignal(
                source="api", lead_id=lead.id, contact_id=contact_b.id
            ),
        )
    with pytest.raises(LeadNotFoundError, match="Lead not found"):
        LeadCaptureService(session).capture(
            workspace.id,
            LeadCaptureSignal(
                source="api", lead_id=foreign_lead.id, contact_id=contact_a.id
            ),
        )


def test_one_contact_can_have_multiple_new_leads(session: Session) -> None:
    workspace = _workspace(session, "capture-multiple-leads")
    contact = CustomerContactService(session).create_contact(workspace, name="Ada")
    service = LeadCaptureService(session)
    first = service.capture(
        workspace.id, LeadCaptureSignal(source="api", contact_id=contact.id)
    )
    second = service.capture(
        workspace.id, LeadCaptureSignal(source="api", contact_id=contact.id)
    )

    assert first.lead_id != second.lead_id
    assert first.contact_id == second.contact_id == contact.id


@pytest.mark.parametrize(
    ("department", "capability", "message"),
    [
        (False, False, "Sales Department is not configured"),
        (True, False, "capture_lead Capability is not configured"),
    ],
)
def test_missing_configuration_fails_before_identity_creation(
    session: Session,
    department: bool,
    capability: bool,
    message: str,
) -> None:
    workspace = _workspace(
        session,
        f"capture-config-{department}-{capability}",
        department=department,
        capability=capability,
    )
    with pytest.raises(LeadCaptureConfigurationError, match=message):
        LeadCaptureService(session).capture(
            workspace.id, LeadCaptureSignal(source="api", name="No Config")
        )
    assert CustomerContactService(session).list_contacts(workspace) == []


def test_metadata_validation_precedes_persistence(session: Session) -> None:
    workspace = _workspace(session, "capture-safe-metadata")
    service = LeadCaptureService(session)

    with pytest.raises(LeadCaptureInputError, match="JSON-safe"):
        service.capture(
            workspace.id,
            LeadCaptureSignal(source="api", name="Ada", metadata={"id": uuid4()}),
        )
    with pytest.raises(LeadCaptureInputError, match="credentials"):
        service.capture(
            workspace.id,
            LeadCaptureSignal(
                source="api", name="Ada", metadata={"nested": {"token": "secret"}}
            ),
        )
    assert CustomerContactService(session).list_contacts(workspace) == []
