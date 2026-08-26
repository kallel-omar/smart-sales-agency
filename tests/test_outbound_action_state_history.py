from tests.test_outbound_action_audit import _headers, _setup


def _url(account: dict, action: dict) -> str:
    return (
        f"/api/integrations/accounts/{account['id']}/outbound-actions/"
        f"{action['id']}/state-history"
    )


def test_state_history_is_safe_ordered_and_bounded(client):
    account, action = _setup(client, "company-a", "generic_webhook")
    action_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(f"{action_url}/deliver", headers=_headers("company-a")).status_code == 200
    assert client.post(f"{action_url}/retry", headers=_headers("company-a")).status_code == 200

    response = client.get(_url(account, action), headers=_headers("company-a"), params={"limit": 1})

    assert response.status_code == 200
    assert response.json() == [{"state": "failed", "event": "failed", "created_at": response.json()[0]["created_at"]}]
    for field in ("content", "payload", "idempotency_key", "credential_hash", "secret_reference"):
        assert field not in response.json()[0]


def test_state_history_is_workspace_scoped_and_validates_limit(client):
    account, action = _setup(client, "company-a")
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201

    assert client.get(_url(account, action), headers=_headers("company-b")).status_code == 404
    assert client.get(_url(account, action), headers=_headers("company-a"), params={"limit": 101}).status_code == 422
