def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _setup(client, slug: str, provider: str = "generic_hmac") -> tuple[dict, dict]:
    assert client.post("/api/workspaces", json={"slug": slug, "name": slug}).status_code == 201
    account = client.post(
        "/api/integrations/accounts", headers=_headers(slug), json={
            "provider": provider, "external_account_id": slug,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        }
    ).json()
    action = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_headers(slug),
        json={"external_target_id": "recipient", "action_type": "send_message", "content": "hello", "idempotency_key": slug},
    ).json()
    return account, action


def test_integration_health_uses_safe_persisted_outbound_state(client):
    account, action = _setup(client, "company-a")
    response = client.get(f"/api/integrations/accounts/{account['id']}/health", headers=_headers("company-a"))
    assert response.status_code == 200
    assert response.json()["health"] == "setup_required"
    assert response.json()["connection_status"] == "configured"
    assert response.json()["pending_action_count"] == 1
    assert response.json()["most_recent_outbound_at"] is not None

    assert client.post(f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver", headers=_headers("company-a")).status_code == 200
    response = client.get(f"/api/integrations/accounts/{account['id']}/health", headers=_headers("company-a"))
    assert response.json()["health"] == "setup_required"
    assert response.json()["recent_delivered_count"] == 1


def test_integration_health_is_read_only_and_workspace_scoped(client):
    account, _ = _setup(client, "company-a")
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201
    assert client.get(f"/api/integrations/accounts/{account['id']}/health", headers=_headers("company-b")).status_code == 404
    before = client.get(f"/api/integrations/accounts/{account['id']}/health", headers=_headers("company-a")).json()
    after = client.get(f"/api/integrations/accounts/{account['id']}/health", headers=_headers("company-a")).json()
    assert before == after
    for sensitive in ("secret_reference", "credential_hash", "content", "payload"):
        assert sensitive not in before
