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
        headers={"X-Workspace-Slug": workspace_slug},
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


def test_valid_inbound_integration_event_creates_sales_reply(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a", "Company A")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    lead_id = create_lead(client, "company-a")
    payload = inbound_event_payload(lead_id)
    headers, body = signed_webhook_request(
        "company-a-key",
        payload,
        event_id="provider-event-123",
    )

    response = client.post(
        "/api/integrations/inbound-events",
        headers=headers,
        content=body,
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


def test_invalid_inbound_integration_event_is_rejected(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a", "Company A")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    lead_id = create_lead(client, "company-a")

    payload = inbound_event_payload(lead_id)
    payload["workspace_id"] = "must-not-be-accepted"

    headers, body = signed_webhook_request("company-a-key", payload)
    response = client.post("/api/integrations/inbound-events", headers=headers, content=body)

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
    assert response.json()["detail"] == "Invalid webhook authentication"


def test_inactive_integration_context_is_rejected(client, integration_account_factory):
    create_workspace(client, "company-a", "Company A")
    integration_account_factory(workspace_id(client, "company-a"), "inactive-key", active=False)
    lead_id = create_lead(client, "company-a")
    response = client.post("/api/integrations/inbound-events", headers={"X-Integration-Key": "inactive-key"}, json=inbound_event_payload(lead_id))
    assert response.status_code == 401


def test_integration_event_cannot_access_another_workspace_lead(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a", "Company A")
    create_workspace(client, "company-b", "Company B")
    integration_account_factory(
        workspace_id(client, "company-a"),
        "company-a-key",
    )
    integration_account_factory(
        workspace_id(client, "company-b"),
        "company-b-key",
    )
    company_b_lead_id = create_lead(client, "company-b")
    payload = inbound_event_payload(company_b_lead_id)
    headers, body = signed_webhook_request("company-a-key", payload)

    response = client.post(
        "/api/integrations/inbound-events",
        headers=headers,
        content=body,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Lead not found"

    history = client.get(
        f"/api/conversations/{company_b_lead_id}",
        headers={"X-Workspace-Slug": "company-b"},
    )

    assert history.status_code == 200
    assert history.json() == []


def test_inbound_event_duplicate_is_acknowledged_without_second_domain_dispatch(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a", "Company A")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    lead_id = create_lead(client, "company-a")
    payload = inbound_event_payload(lead_id)
    payload.pop("external_event_id")
    headers, body = signed_webhook_request("company-a-key", payload)
    headers["X-Integration-Event-Id"] = "provider-event-123"

    first = client.post("/api/integrations/inbound-events", headers=headers, content=body)

    assert first.status_code == 200
    first_history = client.get(
        f"/api/conversations/{lead_id}",
        headers={"X-Workspace-Slug": "company-a"},
    )
    assert first_history.status_code == 200
    assert len(first_history.json()) == 1

    duplicate = client.post("/api/integrations/inbound-events", headers=headers, content=body)

    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "duplicate": True,
        "correlation_id": first.json()["correlation_id"],
    }
    duplicate_history = client.get(
        f"/api/conversations/{lead_id}",
        headers={"X-Workspace-Slug": "company-a"},
    )
    assert duplicate_history.status_code == 200
    assert len(duplicate_history.json()) == len(first_history.json())


def test_same_inbound_event_identifier_is_distinct_per_integration_account(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a", "Company A")
    account_workspace_id = workspace_id(client, "company-a")
    integration_account_factory(account_workspace_id, "company-a-key-1")
    integration_account_factory(account_workspace_id, "company-a-key-2")
    lead_id = create_lead(client, "company-a")
    payload = inbound_event_payload(lead_id)

    first_headers, first_body = signed_webhook_request("company-a-key-1", payload)
    second_headers, second_body = signed_webhook_request("company-a-key-2", payload)
    first_headers["X-Integration-Event-Id"] = "provider-event-123"
    second_headers["X-Integration-Event-Id"] = "provider-event-123"

    assert client.post("/api/integrations/inbound-events", headers=first_headers, content=first_body).status_code == 200
    assert client.post("/api/integrations/inbound-events", headers=second_headers, content=second_body).status_code == 200


def test_inbound_event_identifier_is_workspace_scoped(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a", "Company A")
    create_workspace(client, "company-b", "Company B")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    integration_account_factory(workspace_id(client, "company-b"), "company-b-key")
    lead_a = create_lead(client, "company-a")
    lead_b = create_lead(client, "company-b")
    headers_a, body_a = signed_webhook_request("company-a-key", inbound_event_payload(lead_a))
    headers_b, body_b = signed_webhook_request("company-b-key", inbound_event_payload(lead_b))
    headers_a["X-Integration-Event-Id"] = "provider-event-123"
    headers_b["X-Integration-Event-Id"] = "provider-event-123"

    assert client.post("/api/integrations/inbound-events", headers=headers_a, content=body_a).status_code == 200
    assert client.post("/api/integrations/inbound-events", headers=headers_b, content=body_b).status_code == 200


def test_supplied_inbound_event_identifier_is_validated(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a", "Company A")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    lead_id = create_lead(client, "company-a")

    for event_id in ("", "   ", "invalid/id", "a" * 201):
        headers, body = signed_webhook_request("company-a-key", inbound_event_payload(lead_id))
        headers["X-Integration-Event-Id"] = event_id

        response = client.post(
            "/api/integrations/inbound-events",
            headers=headers,
            content=body,
        )

        assert response.status_code == 422
        assert response.json() == {"detail": "External event identifier is invalid"}


def test_legacy_inbound_event_without_identifier_remains_non_idempotent(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    create_workspace(client, "company-a", "Company A")
    integration_account_factory(workspace_id(client, "company-a"), "company-a-key")
    lead_id = create_lead(client, "company-a")
    payload = inbound_event_payload(lead_id)
    payload.pop("external_event_id")
    headers, body = signed_webhook_request("company-a-key", payload)

    first = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    second = client.post("/api/integrations/inbound-events", headers=headers, content=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert "duplicate" not in second.json()
    history = client.get(
        f"/api/conversations/{lead_id}",
        headers={"X-Workspace-Slug": "company-a"},
    )
    assert history.status_code == 200
    assert len(history.json()) == 2
