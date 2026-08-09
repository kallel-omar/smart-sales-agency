from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.config import Settings
from app.db import get_session
from app.main import app
from app.models import IntegrationAccountAuditEvent
from app.services.integration_account_audit import (
    IntegrationAccountAuditRetentionPolicy,
)


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def provision_account(client, workspace_slug: str, external_account_id: str) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(workspace_slug),
        json={
            "provider": "generic_hmac",
            "external_account_id": external_account_id,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    return response.json()


def set_event_times(client, account_id: str) -> None:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        events = list(
            session.exec(
                select(IntegrationAccountAuditEvent)
                .where(
                    IntegrationAccountAuditEvent.integration_account_id == UUID(account_id)
                )
                .order_by(IntegrationAccountAuditEvent.created_at.asc())
            ).all()
        )
        base_time = datetime(2026, 1, 1, 12, tzinfo=UTC)
        for offset, event in enumerate(events):
            event.created_at = base_time + timedelta(minutes=offset)
            session.add(event)
        session.commit()


def test_workspace_audit_query_filters_safely_and_isolates_tenants(client):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    company_a_account = provision_account(client, "company-a", "company-a-account")
    company_b_account = provision_account(client, "company-b", "company-b-account")

    deactivate = client.post(
        f"/api/integrations/accounts/{company_a_account['id']}/deactivate",
        headers=workspace_headers("company-a"),
    )
    assert deactivate.status_code == 200
    reactivate = client.post(
        f"/api/integrations/accounts/{company_a_account['id']}/reactivate",
        headers=workspace_headers("company-a"),
    )
    assert reactivate.status_code == 200
    set_event_times(client, company_a_account["id"])

    all_events = client.get(
        "/api/integrations/audit-events",
        headers=workspace_headers("company-a"),
    )
    assert all_events.status_code == 200
    assert [event["action"] for event in all_events.json()] == [
        "reactivated",
        "deactivated",
        "provisioned",
    ]
    assert {event["workspace_id"] for event in all_events.json()} == {
        company_a_account["workspace_id"]
    }
    assert company_b_account["id"] not in {
        event["integration_account_id"] for event in all_events.json()
    }
    assert set(all_events.json()[0]) == {
        "id",
        "workspace_id",
        "integration_account_id",
        "action",
        "created_at",
    }

    action_filtered = client.get(
        "/api/integrations/audit-events?action=deactivated",
        headers=workspace_headers("company-a"),
    )
    assert action_filtered.status_code == 200
    assert [event["action"] for event in action_filtered.json()] == ["deactivated"]

    ranged = client.get(
        "/api/integrations/audit-events",
        headers=workspace_headers("company-a"),
        params={
            "created_after": "2026-01-01T12:01:00Z",
            "created_before": "2026-01-01T12:02:00Z",
        },
    )
    assert ranged.status_code == 200
    assert [event["action"] for event in ranged.json()] == ["reactivated", "deactivated"]


def test_audit_queries_enforce_bounds_and_account_scope(client):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    company_a_account = provision_account(client, "company-a", "company-a-account")
    company_b_account = provision_account(client, "company-b", "company-b-account")

    for operation in ("deactivate", "reactivate"):
        response = client.post(
            f"/api/integrations/accounts/{company_a_account['id']}/{operation}",
            headers=workspace_headers("company-a"),
        )
        assert response.status_code == 200
    set_event_times(client, company_a_account["id"])

    account_events = client.get(
        f"/api/integrations/accounts/{company_a_account['id']}/audit-events",
        headers=workspace_headers("company-a"),
        params={"limit": 2},
    )
    assert account_events.status_code == 200
    assert [event["action"] for event in account_events.json()] == [
        "reactivated",
        "deactivated",
    ]

    maximum_bound = client.get(
        "/api/integrations/audit-events",
        headers=workspace_headers("company-a"),
        params={"limit": 101},
    )
    assert maximum_bound.status_code == 422

    invalid_range = client.get(
        "/api/integrations/audit-events",
        headers=workspace_headers("company-a"),
        params={
            "created_after": "2026-01-02T00:00:00Z",
            "created_before": "2026-01-01T00:00:00Z",
        },
    )
    assert invalid_range.status_code == 422

    denied = client.get(
        f"/api/integrations/accounts/{company_b_account['id']}/audit-events",
        headers=workspace_headers("company-a"),
    )
    assert denied.status_code == 404
    assert denied.json()["detail"] == "Integration account not found"

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        persisted_events = session.exec(select(IntegrationAccountAuditEvent)).all()
        assert len(persisted_events) == 4


def test_retention_policy_is_configurable_and_non_destructive():
    policy = IntegrationAccountAuditRetentionPolicy(retention_days=90)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert policy.cutoff(now) == datetime(2025, 10, 3, tzinfo=UTC)

    with pytest.raises(ValueError):
        IntegrationAccountAuditRetentionPolicy(retention_days=0)
    with pytest.raises(ValidationError):
        Settings(integration_account_audit_retention_days=0)
