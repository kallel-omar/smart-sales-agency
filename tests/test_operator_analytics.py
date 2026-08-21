import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlmodel import select

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.capabilities import BusinessCapabilityKey
from app.core.work_items import WorkItemStatus
from app.db import get_session
from app.main import app
from app.models import (
    AIInvocationStatus,
    AIInvocationUsage,
    ApprovalRequest,
    ApprovalStatus,
    Lead,
    LeadStatus,
    WorkItem,
    Workspace,
    utc_now,
)
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService


def _workspace(client, slug: str) -> Workspace:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug})
    assert response.status_code == 201
    with next(app.dependency_overrides[get_session]()) as session:
        return session.exec(select(Workspace).where(Workspace.slug == slug)).one()


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _seed_analytics(client, slug: str):
    workspace = _workspace(client, slug)
    now = utc_now()
    with next(app.dependency_overrides[get_session]()) as session:
        workspace = session.get(Workspace, workspace.id)
        department = DepartmentService(session).ensure_sales_department(workspace)
        employee = AIEmployeeService(session).create_for_department(
            workspace,
            department,
            AIEmployeeRoleKey.FOLLOW_UP,
            name=f"{slug} employee",
        )
        capture = CapabilityService(session).ensure_for_department(
            workspace, department, BusinessCapabilityKey.CAPTURE_LEAD
        )
        qualify = CapabilityService(session).ensure_for_department(
            workspace, department, BusinessCapabilityKey.QUALIFY_LEAD
        )

        completed = WorkItem(
            workspace_id=workspace.id,
            department_id=department.id,
            ai_employee_id=employee.id,
            capability_id=capture.id,
            status=WorkItemStatus.COMPLETED,
            work_type="lead_capture",
            title="Captured lead",
            input={"api_token": "must-not-appear"},
            result={"raw_prompt": "must-not-appear"},
            created_at=now - timedelta(days=2),
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=1),
        )
        failed = WorkItem(
            workspace_id=workspace.id,
            department_id=department.id,
            ai_employee_id=employee.id,
            capability_id=capture.id,
            status=WorkItemStatus.FAILED,
            work_type="lead_capture",
            title="Capture failed",
            created_at=now - timedelta(days=2),
        )
        invalid_duration = WorkItem(
            workspace_id=workspace.id,
            department_id=department.id,
            ai_employee_id=employee.id,
            capability_id=qualify.id,
            status=WorkItemStatus.COMPLETED,
            work_type="lead_qualification",
            title="Qualified lead",
            created_at=now - timedelta(days=1),
            started_at=now,
            completed_at=now - timedelta(hours=1),
        )
        approval_item = WorkItem(
            workspace_id=workspace.id,
            department_id=department.id,
            ai_employee_id=employee.id,
            capability_id=qualify.id,
            status=WorkItemStatus.APPROVAL_REQUIRED,
            work_type="lead_qualification",
            title="Review qualification",
            created_at=now - timedelta(days=1),
        )
        outside = WorkItem(
            workspace_id=workspace.id,
            department_id=department.id,
            status=WorkItemStatus.RUNNING,
            work_type="old_work",
            title="Old work",
            created_at=now - timedelta(days=40),
        )
        session.add_all([completed, failed, invalid_duration, approval_item, outside])
        session.commit()
        for item in [completed, failed, invalid_duration, approval_item, outside]:
            session.refresh(item)

        session.add_all(
            [
                ApprovalRequest(
                    work_item_id=approval_item.id,
                    status=ApprovalStatus.PENDING,
                    payload={"secret": "must-not-appear"},
                    created_at=now - timedelta(hours=5),
                ),
                ApprovalRequest(
                    work_item_id=approval_item.id,
                    status=ApprovalStatus.APPROVED,
                    created_at=now - timedelta(hours=4),
                ),
                ApprovalRequest(
                    work_item_id=completed.id,
                    status=ApprovalStatus.REJECTED,
                    created_at=now - timedelta(hours=3),
                ),
            ]
        )
        session.add_all(
            [
                AIInvocationUsage(
                    workspace_id=workspace.id,
                    department_id=department.id,
                    ai_employee_id=employee.id,
                    capability_id=capture.id,
                    work_item_id=completed.id,
                    task_identifier="capture",
                    agent_identifier="sales",
                    provider="openai",
                    model="gpt-test",
                    input_tokens=100,
                    output_tokens=40,
                    total_tokens=140,
                    latency_ms=10,
                    estimated_cost=Decimal("0.125"),
                    status=AIInvocationStatus.SUCCESSFUL,
                    created_at=now - timedelta(hours=3),
                ),
                AIInvocationUsage(
                    workspace_id=workspace.id,
                    task_identifier="unknown",
                    agent_identifier="sales",
                    provider="other",
                    model="model-x",
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    latency_ms=5,
                    estimated_cost=None,
                    status=AIInvocationStatus.FAILED,
                    created_at=now - timedelta(hours=2),
                ),
                AIInvocationUsage(
                    workspace_id=workspace.id,
                    task_identifier="old",
                    agent_identifier="sales",
                    provider="openai",
                    model="old-model",
                    input_tokens=999,
                    output_tokens=1,
                    total_tokens=1000,
                    latency_ms=5,
                    estimated_cost=Decimal(9),
                    status=AIInvocationStatus.SUCCESSFUL,
                    created_at=now - timedelta(days=40),
                ),
            ]
        )
        session.add_all(
            [
                Lead(
                    tenant_id=workspace.slug,
                    full_name="Recent Won",
                    company_name="Example",
                    status=LeadStatus.WON,
                    created_at=now - timedelta(days=2),
                ),
                Lead(
                    tenant_id=workspace.slug,
                    full_name="Old New",
                    company_name="Example",
                    status=LeadStatus.NEW,
                    created_at=now - timedelta(days=40),
                ),
            ]
        )
        session.commit()
        return SimpleNamespace(employee_id=employee.id, capture_id=capture.id)


def test_operator_analytics_aggregates_persisted_workspace_facts(client):
    own = _seed_analytics(client, "analytics-a")
    _seed_analytics(client, "analytics-b")

    response = client.get("/api/operator/analytics", headers=_headers("analytics-a"))

    assert response.status_code == 200
    body = response.json()
    assert body["period"]["days"] == 30
    assert body["workitems"]["current"] == {
        "created": 0,
        "assigned": 0,
        "running": 1,
        "waiting": 0,
        "approval_required": 1,
        "completed": 2,
        "failed": 1,
        "cancelled": 0,
        "expired": 0,
    }
    assert body["workitems"]["created"] == 4
    assert body["workitems"]["completed"] == 2
    assert body["workitems"]["failed"] == 1
    assert body["workitems"]["success_rate"] == 2 / 3
    assert body["workitems"]["average_completion_seconds"] == 3600
    assert body["approvals"] == {
        "requests_created": 3,
        "pending": 1,
        "approved": 1,
        "rejected": 1,
        "workitems_with_approval_request": 2,
        "approval_request_rate": 0.5,
    }
    workforce = body["workforce"][0]
    assert workforce["employee_id"] == str(own.employee_id)
    assert workforce["workitems"] == 4
    assert workforce["invocation_count"] == 1
    capture = next(
        row for row in body["capabilities"] if row["capability_id"] == str(own.capture_id)
    )
    assert capture["workitems"] == 2
    assert capture["success_rate"] == 0.5
    assert capture["invocation_count"] == 1
    assert body["ai_usage"]["invocation_count"] == 2
    assert body["ai_usage"]["total_tokens"] == 140
    assert body["ai_usage"]["known_estimated_cost"] == "0.12500000"
    assert {row["key"] for row in body["ai_usage"]["by_provider"]} == {"openai", "other"}
    assert {row["key"] for row in body["ai_usage"]["by_model"]} == {"gpt-test", "model-x"}
    assert body["sales"]["total_leads"] == 2
    assert body["sales"]["leads_created"] == 1
    assert body["sales"]["won_leads"] == 1
    assert body["sales"]["by_status"]["won"] == 1
    assert body["sales"]["outcomes"] == {
        "capture_lead_completed": 1,
        "qualification_completed": 1,
        "follow_up_completed": 0,
    }
    serialized = json.dumps(body).casefold()
    assert "must-not-appear" not in serialized
    assert "raw_prompt" not in serialized

    ninety_days = client.get(
        "/api/operator/analytics?days=90", headers=_headers("analytics-a")
    ).json()
    assert ninety_days["workitems"]["created"] == 5
    assert ninety_days["ai_usage"]["total_tokens"] == 1140
    assert ninety_days["sales"]["leads_created"] == 2


def test_operator_analytics_period_options_and_null_rates_are_safe(client):
    _workspace(client, "analytics-empty")

    for days in (7, 30, 90):
        response = client.get(
            f"/api/operator/analytics?days={days}",
            headers=_headers("analytics-empty"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["period"]["days"] == days
        assert body["workitems"]["success_rate"] is None
        assert body["workitems"]["average_completion_seconds"] is None
        assert body["approvals"]["approval_request_rate"] is None
        assert body["ai_usage"]["known_estimated_cost"] == "0"

    invalid = client.get("/api/operator/analytics?days=14", headers=_headers("analytics-empty"))
    assert invalid.status_code == 422
