from uuid import UUID

import pytest

from app.db import get_session
from app.main import app
from app.models import IntegrationAccount
from app.services.secret_resolver import EnvironmentSecretResolver


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def provision_payload(secret_reference: str) -> dict[str, str]:
    return {
        "provider": "generic_hmac",
        "external_account_id": "account-123",
        "secret_reference": secret_reference,
    }


def test_allowed_integration_secret_reference_is_persisted_without_exposure(client):
    create_workspace(client, "company-a")

    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers("company-a"),
        json=provision_payload("INTEGRATION_SECRET_GENERIC_HMAC"),
    )

    assert response.status_code == 201
    assert "secret_reference" not in response.json()
    assert "replace-with-a-secret" not in str(response.json())

    account_id = UUID(response.json()["id"])
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        account = session.get(IntegrationAccount, account_id)
        assert account is not None
        assert account.secret_reference == "INTEGRATION_SECRET_GENERIC_HMAC"


@pytest.mark.parametrize(
    "secret_reference",
    [
        "integration_secret_generic_hmac",
        "INTEGRATION_SECRET_GENERIC-HMAC",
        "INTEGRATION_SECRET_",
        "INTEGRATION_SECRET_GENERIC HMAC",
        " INTEGRATION_SECRET_GENERIC_HMAC",
    ],
)
def test_malformed_secret_reference_is_rejected(client, secret_reference):
    create_workspace(client, "company-a")

    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers("company-a"),
        json=provision_payload(secret_reference),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Secret reference is not allowed"


def test_disallowed_secret_namespace_is_rejected_before_persistence(client):
    create_workspace(client, "company-a")

    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers("company-a"),
        json=provision_payload("DATABASE_URL"),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Secret reference is not allowed"

    accounts = client.get(
        "/api/integrations/accounts",
        headers=workspace_headers("company-a"),
    )
    assert accounts.status_code == 200
    assert accounts.json() == []


def test_environment_resolver_refuses_disallowed_persisted_reference():
    resolver = EnvironmentSecretResolver({"DATABASE_URL": "must-not-be-read"})

    assert resolver.resolve("DATABASE_URL") is None


def test_valid_allowed_reference_continues_to_authenticate(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    workspace = client.get("/api/workspaces/company-a").json()
    integration_account_factory(
        UUID(workspace["id"]),
        "company-a-key",
        secret_reference="INTEGRATION_SECRET_GENERIC_HMAC_TEST",
    )
    lead = client.post(
        "/api/leads",
        json={
            "tenant_id": "company-a",
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
        },
    )
    assert lead.status_code == 201
    payload = {
        "lead_id": lead.json()["id"],
        "channel": "website_chat",
        "content": "What is the monthly price?",
    }
    headers, body = signed_webhook_request("company-a-key", payload)

    response = client.post(
        "/api/integrations/inbound-events",
        headers=headers,
        content=body,
    )

    assert response.status_code == 200
