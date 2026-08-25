from uuid import UUID

from sqlmodel import select

from app.core.capabilities import BusinessCapabilityKey
from app.db import get_session
from app.main import app
from app.models import (
    Capability,
    Contact,
    Department,
    Lead,
    User,
    WorkItem,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)


def _legacy_workspace(slug: str) -> Workspace:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        user = session.exec(select(User)).first()
        assert user is not None
        workspace = Workspace(slug=slug, name=slug)
        session.add(workspace)
        session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceMemberRole.OWNER,
            )
        )
        session.commit()
        session.refresh(workspace)
        return workspace


def test_authenticated_api_lead_creation_uses_generic_capture_boundary(client) -> None:
    workspace_response = client.post(
        "/api/workspaces",
        json={"slug": "website-capture", "name": "Website Capture"},
    )
    assert workspace_response.status_code == 201

    response = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": "website-capture"},
        json={
            "full_name": "Ada Lovelace",
            "company_name": "Analytical Engines",
            "job_title": "Founder",
            "email": "ada@example.test",
            "phone": "+21620000000",
            "website": "https://example.test",
            "source": "website",
            "notes": "Requested a demo",
        },
    )

    assert response.status_code == 201
    data = response.json()
    workspace_id = UUID(workspace_response.json()["id"])
    assert data["job_title"] == "Founder"
    assert data["website"] == "https://example.test"
    assert data["notes"] == "Requested a demo"
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        lead = session.get(Lead, UUID(data["id"]))
        assert lead and lead.tenant_id == "website-capture"
        assert lead.contact_id is not None
        contact = session.get(Contact, lead.contact_id)
        assert contact and contact.workspace_id == workspace_id
        work_item = session.exec(
            select(WorkItem).where(WorkItem.work_type == "lead_capture")
        ).one()
        assert work_item.workspace_id == workspace_id
        assert work_item.status == "completed"
        assert work_item.assignment_id is not None
        assert work_item.ai_employee_id is not None
        assert work_item.input["lead_id"] == data["id"]


def test_api_lead_creation_preserves_workspace_authorization(client) -> None:
    response = client.post(
        "/api/leads",
        headers={"X-Workspace-Slug": "missing-workspace"},
        json={
            "full_name": "Ada Lovelace",
            "company_name": "Analytical Engines",
            "source": "website",
        },
    )

    assert response.status_code == 404


def test_legacy_workspace_api_capture_idempotently_ensures_foundation(client) -> None:
    workspace = _legacy_workspace("legacy-api-capture")
    payload = {
        "full_name": "Ada Lovelace",
        "company_name": "Analytical Engines",
        "source": "website",
    }
    headers = {"X-Workspace-Slug": workspace.slug}

    first = client.post("/api/leads", headers=headers, json=payload)
    second = client.post("/api/leads", headers=headers, json=payload)

    assert first.status_code == second.status_code == 201
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        departments = session.exec(
            select(Department).where(Department.workspace_id == workspace.id)
        ).all()
        capabilities = session.exec(
            select(Capability).where(Capability.workspace_id == workspace.id)
        ).all()
        assert len(departments) == 1
        assert {capability.key for capability in capabilities} == {
            BusinessCapabilityKey.CAPTURE_LEAD,
            BusinessCapabilityKey.RESEARCH_COMPANY,
        }
