from tests.test_outbound_delivery_approval_gate import _create_workspace_and_action, _headers


def _url(account: dict, action: dict) -> str:
    return f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/approval-status"


def test_outbound_approval_status_is_safe_and_read_only(client):
    account, action = _create_workspace_and_action(client)
    response = client.get(_url(account, action), headers=_headers("company-a"))

    assert response.status_code == 200
    assert response.json() == {
        "action_id": action["id"],
        "requires_approval": True,
        "approval_request_id": action["approval_request_id"],
        "approval_status": "pending",
        "decided_by_user_id": None,
        "decided_by_membership_id": None,
        "decided_by_role": None,
    }
    for field in ("content", "payload", "idempotency_key", "credential_hash", "secret_reference"):
        assert field not in response.json()


def test_outbound_approval_status_reflects_decisions_and_is_workspace_scoped(client):
    account, action = _create_workspace_and_action(client)
    approval_id = action["approval_request_id"]
    assert client.post(
        f"/api/approvals/{approval_id}/approve", headers=_headers("company-a"), json={}
    ).status_code == 200
    status = client.get(_url(account, action), headers=_headers("company-a")).json()
    assert status["approval_status"] == "approved"
    assert status["decided_by_user_id"] is not None
    assert status["decided_by_membership_id"] is not None
    assert status["decided_by_role"] == "owner"

    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201
    assert client.get(_url(account, action), headers=_headers("company-b")).status_code == 404
