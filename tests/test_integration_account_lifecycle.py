from hashlib import sha256
from uuid import UUID

from app.db import get_session
from app.main import app
from app.models import IntegrationAccount


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def create_lead(client, workspace_slug: str) -> str:
    response = client.post(
        "/api/leads",
        json={
            "tenant_id": workspace_slug,
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def provision_account(client, workspace_slug: str) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(workspace_slug),
        json={"provider": "generic-channel", "external_account_id": "account-123"},
    )
    assert response.status_code == 201
    return response.json()


def inbound_payload(lead_id: str) -> dict[str, str]:
    return {
        "lead_id": lead_id,
        "channel": "website_chat",
        "content": "What is the monthly price?",
    }


def test_provisioning_returns_raw_credential_once_and_persists_only_its_hash(client):
    create_workspace(client, "company-a")

    created = provision_account(client, "company-a")

    assert created["inbound_credential"]
    assert "credential_hash" not in created

    account_id = UUID(created["id"])
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        account = session.get(IntegrationAccount, account_id)
        assert account is not None
        assert account.credential_hash == sha256(
            created["inbound_credential"].encode()
        ).hexdigest()
        assert account.credential_hash != created["inbound_credential"]

    accounts = client.get(
        "/api/integrations/accounts",
        headers=workspace_headers("company-a"),
    )
    assert accounts.status_code == 200
    assert accounts.json()[0]["id"] == created["id"]
    assert "inbound_credential" not in accounts.json()[0]
    assert "credential_hash" not in accounts.json()[0]


def test_accounts_are_listed_only_in_their_workspace_and_cross_workspace_mutation_is_denied(client):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    company_a_account = provision_account(client, "company-a")
    company_b_account = provision_account(client, "company-b")

    company_a_list = client.get(
        "/api/integrations/accounts",
        headers=workspace_headers("company-a"),
    )
    assert company_a_list.status_code == 200
    assert [account["id"] for account in company_a_list.json()] == [company_a_account["id"]]

    company_b_list = client.get(
        "/api/integrations/accounts",
        headers=workspace_headers("company-b"),
    )
    assert company_b_list.status_code == 200
    assert [account["id"] for account in company_b_list.json()] == [company_b_account["id"]]

    denied = client.post(
        f"/api/integrations/accounts/{company_b_account['id']}/deactivate",
        headers=workspace_headers("company-a"),
    )
    assert denied.status_code == 404
    assert denied.json()["detail"] == "Integration account not found"

    unchanged = client.get(
        "/api/integrations/accounts",
        headers=workspace_headers("company-b"),
    )
    assert unchanged.json()[0]["active"] is True


def test_deactivation_and_reactivation_control_authentication_for_current_credential(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    lead_id = create_lead(client, "company-a")

    deactivated = client.post(
        f"/api/integrations/accounts/{account['id']}/deactivate",
        headers=workspace_headers("company-a"),
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False
    assert "credential_hash" not in deactivated.json()

    denied = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": account["inbound_credential"]},
        json=inbound_payload(lead_id),
    )
    assert denied.status_code == 401

    reactivated = client.post(
        f"/api/integrations/accounts/{account['id']}/reactivate",
        headers=workspace_headers("company-a"),
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["active"] is True
    assert "credential_hash" not in reactivated.json()

    accepted = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": account["inbound_credential"]},
        json=inbound_payload(lead_id),
    )
    assert accepted.status_code == 200


def test_rotation_immediately_invalidates_previous_credential_and_authenticates_new_one(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    lead_id = create_lead(client, "company-a")

    rotated = client.post(
        f"/api/integrations/accounts/{account['id']}/credential/rotate",
        headers=workspace_headers("company-a"),
    )
    assert rotated.status_code == 200
    rotated_data = rotated.json()
    assert rotated_data["inbound_credential"] != account["inbound_credential"]
    assert "credential_hash" not in rotated_data

    old_credential = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": account["inbound_credential"]},
        json=inbound_payload(lead_id),
    )
    assert old_credential.status_code == 401

    new_credential = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": rotated_data["inbound_credential"]},
        json=inbound_payload(lead_id),
    )
    assert new_credential.status_code == 200
