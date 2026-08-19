from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.capabilities import (
    BusinessCapabilityKey,
    SALES_BUSINESS_CAPABILITY_KEYS,
)
from app.core.events import Department as DepartmentKind
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.services import SalesDepartmentService
from app.departments.sales.supervisor import SalesEvent
from app.models import Workspace
from app.schemas import CapabilityRead
from app.services.capabilities import (
    CapabilityNotFoundError,
    CapabilityService,
    DepartmentWorkspaceMismatchError,
    DuplicateCapabilityError,
    UnsupportedCapabilityError,
)
from app.services.departments import DepartmentService


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


def test_capability_is_workspace_and_department_scoped(session: Session) -> None:
    workspace = _workspace(session, "capability-a")
    department = DepartmentService(session).ensure_sales_department(workspace)
    service = CapabilityService(session)

    capability = service.ensure_for_department(
        workspace,
        department,
        BusinessCapabilityKey.RESEARCH_COMPANY,
    )
    read = CapabilityRead.model_validate(capability)

    assert read.workspace_id == workspace.id
    assert read.department_id == department.id
    assert read.key is BusinessCapabilityKey.RESEARCH_COMPANY
    assert read.active is True
    assert service.list_for_department(workspace, department) == [capability]


def test_same_capability_is_allowed_in_different_workspaces(session: Session) -> None:
    workspace_a = _workspace(session, "capability-boundary-a")
    workspace_b = _workspace(session, "capability-boundary-b")
    department_service = DepartmentService(session)
    department_a = department_service.ensure_sales_department(workspace_a)
    department_b = department_service.ensure_sales_department(workspace_b)
    service = CapabilityService(session)

    capability_a = service.ensure_for_department(
        workspace_a,
        department_a,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    )
    capability_b = service.ensure_for_department(
        workspace_b,
        department_b,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
    )

    assert capability_a.id != capability_b.id
    assert capability_a.workspace_id == workspace_a.id
    assert capability_b.workspace_id == workspace_b.id


def test_workspace_cannot_access_another_workspace_capability(
    session: Session,
) -> None:
    workspace_a = _workspace(session, "capability-hidden-a")
    workspace_b = _workspace(session, "capability-hidden-b")
    department = DepartmentService(session).ensure_sales_department(workspace_a)
    service = CapabilityService(session)
    capability = service.ensure_for_department(
        workspace_a,
        department,
        BusinessCapabilityKey.SEND_MESSAGE,
    )

    with pytest.raises(CapabilityNotFoundError, match="Capability not found"):
        service.get_for_workspace(workspace_b, capability.id)


def test_department_must_belong_to_selected_workspace(session: Session) -> None:
    workspace_a = _workspace(session, "capability-department-a")
    workspace_b = _workspace(session, "capability-department-b")
    department_a = DepartmentService(session).ensure_sales_department(workspace_a)

    with pytest.raises(
        DepartmentWorkspaceMismatchError,
        match="Department does not belong to this workspace",
    ):
        CapabilityService(session).ensure_for_department(
            workspace_b,
            department_a,
            BusinessCapabilityKey.QUALIFY_LEAD,
        )


def test_duplicate_and_unsupported_capability_behavior(session: Session) -> None:
    workspace = _workspace(session, "capability-constraints")
    department = DepartmentService(session).ensure_sales_department(workspace)
    service = CapabilityService(session)

    created = service.create_for_department(
        workspace,
        department,
        BusinessCapabilityKey.CAPTURE_LEAD,
    )

    with pytest.raises(DuplicateCapabilityError, match="already has this capability"):
        service.create_for_department(
            workspace,
            department,
            BusinessCapabilityKey.CAPTURE_LEAD,
        )
    with pytest.raises(UnsupportedCapabilityError, match="not registered"):
        service.ensure_for_department(
            workspace,
            department,
            "not-a-capability",  # type: ignore[arg-type]
        )

    assert (
        service.ensure_for_department(
            workspace,
            department,
            BusinessCapabilityKey.CAPTURE_LEAD,
        ).id
        == created.id
    )


def test_initial_sales_capabilities_are_registered(session: Session) -> None:
    workspace = _workspace(session, "capability-sales")
    department = DepartmentService(session).ensure_sales_department(workspace)

    capabilities = CapabilityService(session).ensure_sales_capabilities(
        workspace,
        department,
    )

    assert [capability.key for capability in capabilities] == list(
        SALES_BUSINESS_CAPABILITY_KEYS
    )
    assert {
        BusinessCapabilityKey.CAPTURE_LEAD,
        BusinessCapabilityKey.RESEARCH_COMPANY,
        BusinessCapabilityKey.QUALIFY_LEAD,
        BusinessCapabilityKey.ANSWER_CUSTOMER,
        BusinessCapabilityKey.SEND_MESSAGE,
        BusinessCapabilityKey.FOLLOW_UP_LEAD,
    } == {capability.key for capability in capabilities}


def test_existing_sales_department_service_remains_compatible(session: Session) -> None:
    workspace = _workspace(session, "capability-sales-compat")
    department = DepartmentService(session).ensure_sales_department(workspace)
    CapabilityService(session).ensure_sales_capabilities(workspace, department)
    sales_service = SalesDepartmentService(
        AgentContext(
            settings=object(),
            repository=object(),
            llm=None,
            workspace=workspace,
        )
    )

    assert sales_service.supervisor.route(SalesEvent.NEW_LEAD) == "research_and_qualify"
    assert sales_service.supervisor.route(SalesEvent.INBOUND_MESSAGE) == "sales_conversation"
