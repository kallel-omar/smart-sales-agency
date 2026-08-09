from uuid import UUID


def create_workspace(client, slug: str) -> None:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.title()})
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


def workspace_id(client, slug: str) -> UUID:
    return UUID(client.get(f"/api/workspaces/{slug}").json()["id"])


def inbound_payload(lead_id: str) -> dict[str, str]:
    return {
        "lead_id": lead_id,
        "channel": "website_chat",
        "content": "What is the monthly price?",
        "external_event_id": "event-123",
    }


def test_invalid_and_missing_webhook_signatures_are_rejected(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    lead_id = create_lead(client, "company-a")

    headers, body = signed_webhook_request(
        "company-a-key",
        inbound_payload(lead_id),
        signature="not-a-valid-signature",
    )
    invalid = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "Invalid webhook authentication"

    missing = client.post(
        "/api/integrations/inbound-events",
        headers={"X-Integration-Key": "company-a-key"},
        json=inbound_payload(lead_id),
    )
    assert missing.status_code == 401
    assert missing.json()["detail"] == "Invalid webhook authentication"


def test_unknown_provider_verifier_is_rejected_safely(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    integration_account_factory(
        workspace_id(client, "company-a"),
        "company-a-key",
        provider="unknown_provider",
    )
    lead_id = create_lead(client, "company-a")
    headers, body = signed_webhook_request("company-a-key", inbound_payload(lead_id))

    response = client.post("/api/integrations/inbound-events", headers=headers, content=body)

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook authentication"


def test_signed_webhook_body_cannot_bypass_verification(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    lead_id = create_lead(client, "company-a")
    signed_payload = inbound_payload(lead_id)
    headers, _ = signed_webhook_request("company-a-key", signed_payload)
    replacement_payload = {**signed_payload, "content": "tampered after signing"}

    response = client.post(
        "/api/integrations/inbound-events",
        headers=headers,
        json=replacement_payload,
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook authentication"
