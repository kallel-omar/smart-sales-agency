from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.event_factory import create_business_event
from app.core.event_payloads import InboundSalesMessagePayload
from app.core.event_types import EventType
from app.core.events import Department as DepartmentKind
from app.departments.sales.agents.base import AgentContext
from app.departments.sales.services import SalesDepartmentService
from app.departments.sales.supervisor import SalesEvent
from app.models import Workspace
from app.schemas import DepartmentRead
from app.services.departments import (
    DepartmentNotFoundError,
    DepartmentService,
    DuplicateDepartmentError,
    UnsupportedDepartmentError,
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


def test_department_is_workspace_scoped(session: Session) -> None:
    workspace_a = _workspace(session, "department-a")
    workspace_b = _workspace(session, "department-b")
    service = DepartmentService(session)

    department_a = service.ensure_sales_department(workspace_a)
    department_b = service.ensure_sales_department(workspace_b)

    assert department_a.workspace_id == workspace_a.id
    assert department_b.workspace_id == workspace_b.id
    assert department_a.id != department_b.id
    assert [department.id for department in service.list_for_workspace(workspace_a)] == [
        department_a.id
    ]
    assert [department.id for department in service.list_for_workspace(workspace_b)] == [
        department_b.id
    ]


def test_sales_department_uses_existing_department_contract(session: Session) -> None:
    workspace = _workspace(session, "department-sales")
    stored = DepartmentService(session).ensure_sales_department(workspace)
    read = DepartmentRead.model_validate(stored)

    event = create_business_event(
        workspace_id=workspace.id,
        event_type=EventType.SALES_INBOUND_MESSAGE,
        source_department=DepartmentKind.PLATFORM,
        destination_department=read.kind,
        payload=InboundSalesMessagePayload(
            lead_id="00000000-0000-0000-0000-000000000001",
            channel="web",
            content="Hello",
        ),
    )

    assert read.kind is DepartmentKind.SALES
    assert event.destination_department is DepartmentKind.SALES


def test_workspace_cannot_access_another_workspace_department(
    session: Session,
) -> None:
    workspace_a = _workspace(session, "department-boundary-a")
    workspace_b = _workspace(session, "department-boundary-b")
    service = DepartmentService(session)
    department_a = service.ensure_sales_department(workspace_a)

    with pytest.raises(DepartmentNotFoundError, match="Department not found"):
        service.get_for_workspace(workspace_b, department_a.id)


def test_invalid_and_duplicate_department_behavior(session: Session) -> None:
    workspace = _workspace(session, "department-constraints")
    service = DepartmentService(session)

    created = service.create_for_workspace(workspace, DepartmentKind.SALES)

    with pytest.raises(DuplicateDepartmentError, match="already has this department"):
        service.create_for_workspace(workspace, DepartmentKind.SALES)
    with pytest.raises(UnsupportedDepartmentError, match="not registered"):
        service.ensure_for_workspace(workspace, DepartmentKind.MARKETING)
    with pytest.raises(UnsupportedDepartmentError, match="not registered"):
        service.ensure_for_workspace(workspace, "not-a-department")  # type: ignore[arg-type]

    assert service.ensure_sales_department(workspace).id == created.id


def test_existing_sales_department_service_remains_compatible(session: Session) -> None:
    workspace = _workspace(session, "department-sales-compat")
    DepartmentService(session).ensure_sales_department(workspace)
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
