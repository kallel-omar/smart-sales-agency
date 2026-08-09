def _headers(slug): return {"X-Workspace-Slug": slug}


def _setup(client, slug="company-a"):
    assert client.post("/api/workspaces", json={"slug": slug, "name": slug}).status_code == 201
    account = client.post("/api/integrations/accounts", headers=_headers(slug), json={"provider":"generic_hmac","external_account_id":slug,"secret_reference":"INTEGRATION_SECRET_GENERIC_HMAC_TEST"}).json()
    action = client.post(f"/api/integrations/accounts/{account['id']}/outbound-actions", headers=_headers(slug), json={"external_target_id":"recipient","action_type":"send_message","content":"private","idempotency_key":f"cancel-{slug}"}).json()
    return account, action


def test_pending_action_can_be_cancelled_once_and_not_delivered(client):
    account, action = _setup(client)
    url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    response = client.post(f"{url}/cancel", headers=_headers("company-a"))
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancelled_at"] is not None
    assert client.post(f"{url}/deliver", headers=_headers("company-a")).status_code == 409
    assert client.post(f"{url}/cancel", headers=_headers("company-a")).status_code == 409


def test_cancellation_is_workspace_scoped_and_terminal_actions_are_denied(client):
    account, action = _setup(client)
    assert client.post("/api/workspaces", json={"slug":"company-b","name":"company-b"}).status_code == 201
    url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(f"{url}/cancel", headers=_headers("company-b")).status_code == 404
    assert client.post(f"{url}/deliver", headers=_headers("company-a")).status_code == 200
    assert client.post(f"{url}/cancel", headers=_headers("company-a")).status_code == 409
