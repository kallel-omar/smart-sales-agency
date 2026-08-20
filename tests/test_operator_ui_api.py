import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlmodel import select

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.capabilities import BusinessCapabilityKey
from app.db import get_session
from app.main import app
from app.models import (
    ApprovalRequest,
    IntegrationAccount,
    OutboundIntegrationActionType,
    Workspace,
    utc_now,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employee_tool_access import AIEmployeeCapabilityToolAccessService
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService
from app.services.work_items import WorkItemService


def _workspace(client, slug: str) -> Workspace:
    response = client.post(
        "/api/workspaces", json={"slug": slug, "name": slug.replace("-", " ")}
    )
    assert response.status_code == 201
    with next(app.dependency_overrides[get_session]()) as session:
        return session.exec(select(Workspace).where(Workspace.slug == slug)).one()


def _seed(client, slug: str):
    workspace = _workspace(client, slug)
    with next(app.dependency_overrides[get_session]()) as session:
        workspace = session.get(Workspace, workspace.id)
        department = DepartmentService(session).ensure_sales_department(workspace)
        employee = AIEmployeeService(session).create_for_department(
            workspace,
            department,
            AIEmployeeRoleKey.FOLLOW_UP,
            name=f"{slug} Follow-up",
        )
        capability = CapabilityService(session).ensure_for_department(
            workspace, department, BusinessCapabilityKey.FOLLOW_UP_LEAD
        )
        assignment = AIEmployeeCapabilityAssignmentService(session).assign(
            workspace, employee, capability
        )
        send_capability = CapabilityService(session).ensure_for_department(
            workspace, department, BusinessCapabilityKey.SEND_MESSAGE
        )
        send_assignment = AIEmployeeCapabilityAssignmentService(session).assign(
            workspace, employee, send_capability
        )
        account = IntegrationAccount(
            workspace_id=workspace.id,
            provider="generic_hmac",
            external_account_id=f"operator-{slug}",
            secret_reference="DO_NOT_EXPOSE_OPERATOR_SECRET",
            credential_hash=uuid4().hex,
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        AIEmployeeCapabilityToolAccessService(session).grant(
            workspace,
            send_assignment,
            account,
            OutboundIntegrationActionType.SEND_MESSAGE,
            AIEmployeeAutonomyLevel.DRAFT_REQUIRES_APPROVAL,
        )
        older = WorkItemService(session).create_work_item(
            workspace,
            department,
            work_type="older_task",
            title="Older task",
            capability=capability,
            input={"lead_id": "safe", "api_token": "never-return-this"},
        )
        newer = WorkItemService(session).create_work_item(
            workspace,
            department,
            work_type="review_message",
            title="Review proposed message",
            capability=capability,
            input={
                "message": "A safe proposed message",
                "nested": {"password": "hidden", "context": "safe"},
            },
        )
        older.created_at = utc_now() - timedelta(days=1)
        session.add(older)
        session.commit()
        newer = WorkItemService(session).assign_work_item(
            workspace, newer.id, assignment
        )
        approval = ApprovalRequest(
            work_item_id=newer.id,
            action_type="send_message",
            channel="email",
            payload={
                "message": "A safe proposed message",
                "integration_account_id": str(account.id),
                "recipient": "customer@example.test",
                "access_token": "never-return-this",
            },
        )
        rejected = ApprovalRequest(
            work_item_id=older.id,
            action_type="review",
            channel="console",
            payload={"reason": "Review this work"},
        )
        session.add(approval)
        session.add(rejected)
        session.commit()
        session.refresh(approval)
        session.refresh(rejected)
        return SimpleNamespace(
            workspace_id=workspace.id,
            employee_id=employee.id,
            assignment_id=assignment.id,
            capability_id=capability.id,
            older_id=older.id,
            newer_id=newer.id,
            approval_id=approval.id,
            rejected_id=rejected.id,
        )


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def test_workforce_operator_api_is_joined_safe_and_workspace_scoped(client):
    own = _seed(client, "operator-workforce-a")
    other = _seed(client, "operator-workforce-b")

    response = client.get(
        "/api/operator/workforce", headers=_headers("operator-workforce-a")
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    employee = response.json()[0]
    assert employee["id"] == str(own.employee_id)
    assert employee["department"] == "sales"
    assert {item["key"] for item in employee["capabilities"]} == {
        "follow_up_lead",
        "send_message",
    }
    access = next(
        item for item in employee["capabilities"] if item["key"] == "send_message"
    )["tool_access"][0]
    assert access == {
        "integration_account_id": access["integration_account_id"],
        "provider": "generic_hmac",
        "external_account_id": "operator-operator-workforce-a",
        "action_type": "send_message",
        "autonomy_level": "draft_requires_approval",
        "active": True,
    }
    assert "secret" not in json.dumps(response.json()).casefold()
    hidden = client.get(
        f"/api/operator/workforce/{other.employee_id}",
        headers=_headers("operator-workforce-a"),
    )
    assert hidden.status_code == 404


def test_work_item_operator_api_filters_limits_orders_and_sanitizes(client):
    own = _seed(client, "operator-work-a")
    other = _seed(client, "operator-work-b")

    listed = client.get(
        "/api/operator/work-items?limit=1", headers=_headers("operator-work-a")
    )
    filtered = client.get(
        "/api/operator/work-items?status=assigned&work_type=review_message",
        headers=_headers("operator-work-a"),
    )
    detail = client.get(
        f"/api/operator/work-items/{own.newer_id}",
        headers=_headers("operator-work-a"),
    )

    assert listed.status_code == filtered.status_code == detail.status_code == 200
    assert [item["id"] for item in listed.json()] == [str(own.newer_id)]
    assert [item["id"] for item in filtered.json()] == [str(own.newer_id)]
    body = detail.json()
    assert body["status"] == "assigned"
    assert body["department"] == "sales"
    assert body["ai_employee_name"] == "operator-work-a Follow-up"
    assert body["capability_key"] == "follow_up_lead"
    assert body["approval_id"] == str(own.approval_id)
    assert body["input"] == {
        "message": "A safe proposed message",
        "nested": {"context": "safe"},
    }
    assert "token" not in json.dumps(body).casefold()
    hidden = client.get(
        f"/api/operator/work-items/{other.newer_id}",
        headers=_headers("operator-work-a"),
    )
    assert hidden.status_code == 404


def test_approval_operator_api_context_and_existing_decisions_are_scoped(client):
    own = _seed(client, "operator-approval-a")
    other = _seed(client, "operator-approval-b")

    listed = client.get(
        "/api/operator/approvals?limit=10",
        headers=_headers("operator-approval-a"),
    )

    assert listed.status_code == 200
    assert {row["id"] for row in listed.json()} == {
        str(own.approval_id),
        str(own.rejected_id),
    }
    approval = next(
        row for row in listed.json() if row["id"] == str(own.approval_id)
    )
    assert approval["work_item_title"] == "Review proposed message"
    assert approval["work_type"] == "review_message"
    assert approval["ai_employee_name"] == "operator-approval-a Follow-up"
    assert approval["capability_key"] == "follow_up_lead"
    assert approval["integration_provider"] == "generic_hmac"
    assert approval["payload"]["message"] == "A safe proposed message"
    assert "token" not in json.dumps(approval).casefold()

    approved = client.post(
        f"/api/approvals/{own.approval_id}/approve",
        headers=_headers("operator-approval-a"),
        json={"reviewer_note": "Approved in operator UI"},
    )
    rejected = client.post(
        f"/api/approvals/{own.rejected_id}/reject",
        headers=_headers("operator-approval-a"),
        json={"reviewer_note": "Rejected in operator UI"},
    )
    cross_workspace = client.post(
        f"/api/approvals/{other.approval_id}/approve",
        headers=_headers("operator-approval-a"),
        json={"reviewer_note": "Must not work"},
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert cross_workspace.status_code == 404
