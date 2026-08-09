from tests.test_outbound_delivery_approval_gate import _create_workspace_and_action, _headers
from tests.test_outbound_action_audit import _setup


def _url(account: dict, action: dict) -> str:
    return (
        f"/api/integrations/accounts/{account['id']}/outbound-actions/"
        f"{action['id']}/timeline"
    )


def test_timeline_composes_safe_chronological_outbound_records(client):
    account, action = _create_workspace_and_action(client, "company-a")
    action_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(
        f"/api/approvals/{action['approval_request_id']}/approve",
        headers=_headers("company-a"),
        json={},
    ).status_code == 200
    assert client.post(f"{action_url}/deliver", headers=_headers("company-a")).status_code == 200

    response = client.get(_url(account, action), headers=_headers("company-a"))

    assert response.status_code == 200
    entries = response.json()
    assert [entry["event"] for entry in entries] == [
        "approval_requested",
        "action_created",
        "approval_approved",
        "delivery_attempt",
        "delivery_attempted",
        "action_failed",
    ]
    assert [entry["category"] for entry in entries] == [
        "approval",
        "lifecycle",
        "approval",
        "delivery",
        "delivery",
        "lifecycle",
    ]
    assert entries == sorted(entries, key=lambda entry: entry["created_at"])
    attempt = next(entry for entry in entries if entry["event"] == "delivery_attempt")
    assert attempt["attempt_number"] == 1
    assert attempt["state"] == "failed"
    for entry in entries:
        for field in (
            "content",
            "payload",
            "idempotency_key",
            "credential_hash",
            "secret_reference",
            "reviewer_note",
        ):
            assert field not in entry


def test_timeline_is_bounded_and_workspace_scoped(client):
    account, action = _setup(client, "company-a", "missing-provider")
    action_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(f"{action_url}/deliver", headers=_headers("company-a")).status_code == 200
    assert client.post(f"{action_url}/retry", headers=_headers("company-a")).status_code == 200
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201

    response = client.get(
        _url(account, action), headers=_headers("company-a"), params={"limit": 2}
    )

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert client.get(_url(account, action), headers=_headers("company-b")).status_code == 404
    assert client.get(
        _url(account, action), headers=_headers("company-a"), params={"limit": 101}
    ).status_code == 422
