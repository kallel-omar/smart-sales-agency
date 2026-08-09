from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import IntegrationAccountAuditEvent


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def provision_account(client, workspace_slug: str) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(workspace_slug),
        json={
            "provider": "generic_hmac",
            "external_account_id": "audit-account",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    return response.json()


def account_events(client, workspace_slug: str, account_id: str) -> list[dict]:
    response = client.get(
        f"/api/integrations/accounts/{account_id}/audit-events",
        headers=workspace_headers(workspace_slug),
    )
    assert response.status_code == 200
    return response.json()


def test_provision_creates_a_safe_workspace_scoped_audit_event(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")

    events = account_events(client, "company-a", account["id"])

    assert [event["action"] for event in events] == ["provisioned"]
    assert events[0]["integration_account_id"] == account["id"]
    assert events[0]["workspace_id"] == account["workspace_id"]
    serialized_event = str(events[0])
    for sensitive_field in (
        "inbound_credential",
        "credential_hash",
        "secret_reference",
        "test-generic-hmac-secret",
    ):
        assert sensitive_field not in serialized_event

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        persisted_event = session.get(IntegrationAccountAuditEvent, UUID(events[0]["id"]))
        assert persisted_event is not None
        assert {
            "inbound_credential",
            "credential_hash",
            "secret_reference",
        }.isdisjoint(persisted_event.__dict__)
        assert "test-generic-hmac-secret" not in {
            str(value) for value in persisted_event.__dict__.values()
        }


def test_lifecycle_operations_record_only_safe_audit_actions(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")

    deactivate = client.post(
        f"/api/integrations/accounts/{account['id']}/deactivate",
        headers=workspace_headers("company-a"),
    )
    assert deactivate.status_code == 200
    reactivate = client.post(
        f"/api/integrations/accounts/{account['id']}/reactivate",
        headers=workspace_headers("company-a"),
    )
    assert reactivate.status_code == 200
    rotate = client.post(
        f"/api/integrations/accounts/{account['id']}/credential/rotate",
        headers=workspace_headers("company-a"),
    )
    assert rotate.status_code == 200
    updated = client.post(
        f"/api/integrations/accounts/{account['id']}/secret-reference",
        headers=workspace_headers("company-a"),
        json={"secret_reference": "INTEGRATION_SECRET_AUDIT_UPDATED"},
    )
    assert updated.status_code == 200

    events = account_events(client, "company-a", account["id"])

    assert [event["action"] for event in events] == [
        "secret_reference_changed",
        "credential_rotated",
        "reactivated",
        "deactivated",
        "provisioned",
    ]
    assert "INTEGRATION_SECRET_AUDIT_UPDATED" not in str(events)
    assert account["inbound_credential"] not in str(events)
    assert rotate.json()["inbound_credential"] not in str(events)


def test_audit_history_isolated_by_workspace_and_failed_operations_do_not_add_events(client):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    company_a_account = provision_account(client, "company-a")
    company_b_account = provision_account(client, "company-b")

    denied_visibility = client.get(
        f"/api/integrations/accounts/{company_b_account['id']}/audit-events",
        headers=workspace_headers("company-a"),
    )
    assert denied_visibility.status_code == 404
    assert denied_visibility.json()["detail"] == "Integration account not found"

    before = account_events(client, "company-a", company_a_account["id"])
    rejected = client.post(
        f"/api/integrations/accounts/{company_a_account['id']}/secret-reference",
        headers=workspace_headers("company-a"),
        json={"secret_reference": "DATABASE_URL"},
    )
    assert rejected.status_code == 422
    cross_workspace_mutation = client.post(
        f"/api/integrations/accounts/{company_b_account['id']}/deactivate",
        headers=workspace_headers("company-a"),
    )
    assert cross_workspace_mutation.status_code == 404

    after = account_events(client, "company-a", company_a_account["id"])
    assert after == before

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        persisted_events = session.exec(select(IntegrationAccountAuditEvent)).all()
        assert len(persisted_events) == 2
