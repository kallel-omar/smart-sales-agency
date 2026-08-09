from uuid import UUID


def create_workspace(client, slug: str, name: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": name},
    )

    assert response.status_code == 201


def create_lead(client, workspace_slug: str) -> str:
    response = client.post(
        "/api/leads",
        json={
            "tenant_id": workspace_slug,
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
            "email": "sarra@example.com",
            "source": "manual",
        },
    )

    assert response.status_code == 201
    return response.json()["id"]


def inbound_event_payload(lead_id: str) -> dict[str, str]:
    return {
        "lead_id": lead_id,
        "channel": "website_chat",
        "content": "What is the monthly price?",
        "external_event_id": "event-123",
    }


def workspace_id(client, slug: str) -> UUID:
    return UUID(client.get(f"/api/workspaces/{slug}").json()["id"])


def test_valid_inbound_integration_event_creates_sales_reply(client, integration_account_factory):
    create_workspace(client, "company-a", "Company A")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    lead_id = create_lead(client, "company-a")

    response = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": "company-a-key"},
        json=inbound_event_payload(lead_id),
    )

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["lead_id"] == lead_id
    assert response_data["approval_id"] is not None

    history = client.get(
        f"/api/conversations/{lead_id}",
        headers={"X-Workspace-Slug": "company-a"},
    )

    assert history.status_code == 200
    assert history.json()[0]["direction"] == "inbound"
    assert history.json()[0]["channel"] == "website_chat"


def test_invalid_inbound_integration_event_is_rejected(client, integration_account_factory):
    create_workspace(client, "company-a", "Company A")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    lead_id = create_lead(client, "company-a")

    payload = inbound_event_payload(lead_id)
    payload["workspace_id"] = "must-not-be-accepted"

    response = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": "company-a-key"},
        json=payload,
    )

    assert response.status_code == 422


def test_unknown_integration_context_is_rejected(client):
    create_workspace(client, "company-a", "Company A")
    lead_id = create_lead(client, "company-a")

    response = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": "unknown-development-key"},
        json=inbound_event_payload(lead_id),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid integration context"


def test_inactive_integration_context_is_rejected(client, integration_account_factory):
    create_workspace(client, "company-a", "Company A")
    integration_account_factory(workspace_id(client, "company-a"), "inactive-key", active=False)
    lead_id = create_lead(client, "company-a")
    response = client.post("/api/integrations/inbound-events", headers={"X-Integration-Key": "inactive-key"}, json=inbound_event_payload(lead_id))
    assert response.status_code == 401


def test_integration_event_cannot_access_another_workspace_lead(client, integration_account_factory):
    create_workspace(client, "company-a", "Company A")
    create_workspace(client, "company-b", "Company B")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    integration_account_factory(workspace_id(client, "company-b"), "company-b-key")
    company_b_lead_id = create_lead(client, "company-b")

    response = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": "company-a-key"},
        json=inbound_event_payload(company_b_lead_id),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Lead not found"

    history = client.get(
        f"/api/conversations/{company_b_lead_id}",
        headers={"X-Workspace-Slug": "company-b"},
    )

    assert history.status_code == 200
    assert history.json() == []
