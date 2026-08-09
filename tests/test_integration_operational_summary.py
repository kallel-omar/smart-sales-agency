def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _account(client, slug: str, provider: str) -> dict:
    return client.post(
        "/api/integrations/accounts", headers=_headers(slug), json={
            "provider": provider, "external_account_id": provider,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        }
    ).json()


def _action(client, slug: str, account_id: str, key: str) -> dict:
    return client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions", headers=_headers(slug), json={
            "external_target_id": key, "action_type": "send_message", "content": "private", "idempotency_key": key,
        }
    ).json()


def test_operational_summary_aggregates_only_current_workspace_and_is_read_only(client):
    assert client.post("/api/workspaces", json={"slug": "company-a", "name": "Company A"}).status_code == 201
    delivered_account = _account(client, "company-a", "generic_hmac")
    failed_account = _account(client, "company-a", "missing-provider")
    pending = _action(client, "company-a", delivered_account["id"], "pending")
    delivered = _action(client, "company-a", delivered_account["id"], "delivered")
    failed = _action(client, "company-a", failed_account["id"], "failed")
    assert client.post(f"/api/integrations/accounts/{delivered_account['id']}/outbound-actions/{delivered['id']}/deliver", headers=_headers("company-a")).status_code == 200
    assert client.post(f"/api/integrations/accounts/{failed_account['id']}/outbound-actions/{failed['id']}/deliver", headers=_headers("company-a")).status_code == 200

    response = client.get("/api/integrations/operational-summary", headers=_headers("company-a"))
    assert response.status_code == 200
    summary = response.json()
    assert summary["active_integration_account_count"] == 2
    assert summary["pending_outbound_action_count"] == 1
    assert summary["delivered_outbound_action_count"] == 1
    assert summary["failed_outbound_action_count"] == 1
    assert summary["retryable_failed_action_count"] == 1
    assert summary["recent_delivered_count"] == 1
    assert summary["recent_failed_count"] == 1
    assert summary["priority_counts"] == {
        "low": 0,
        "normal": 3,
        "high": 0,
        "urgent": 0,
    }
    assert summary["owned_outbound_action_count"] == 0
    assert summary["unowned_outbound_action_count"] == 3
    assert summary["most_recent_outbound_at"] is not None
    for sensitive in ("content", "payload", "secret_reference", "credential_hash"):
        assert sensitive not in summary

    again = client.get("/api/integrations/operational-summary", headers=_headers("company-a"))
    assert again.json() == summary
    assert pending["id"]


def test_operational_summary_is_workspace_scoped(client):
    assert client.post("/api/workspaces", json={"slug": "company-a", "name": "Company A"}).status_code == 201
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "Company B"}).status_code == 201
    account = _account(client, "company-b", "generic_hmac")
    _action(client, "company-b", account["id"], "company-b-action")
    summary = client.get("/api/integrations/operational-summary", headers=_headers("company-a"))
    assert summary.status_code == 200
    assert summary.json()["active_integration_account_count"] == 0
    assert summary.json()["pending_outbound_action_count"] == 0
    assert summary.json()["priority_counts"] == {
        "low": 0,
        "normal": 0,
        "high": 0,
        "urgent": 0,
    }
    assert summary.json()["owned_outbound_action_count"] == 0
    assert summary.json()["unowned_outbound_action_count"] == 0


def test_operational_summary_counts_each_priority_within_the_current_workspace(client):
    assert client.post("/api/workspaces", json={"slug": "company-a", "name": "Company A"}).status_code == 201
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "Company B"}).status_code == 201
    account_a = _account(client, "company-a", "generic_hmac")
    account_b = _account(client, "company-b", "generic_hmac")
    low = _action(client, "company-a", account_a["id"], "low")
    high = _action(client, "company-a", account_a["id"], "high")
    _action(client, "company-b", account_b["id"], "company-b")

    for action_id, priority in ((low["id"], "low"), (high["id"], "high")):
        assert client.put(
            f"/api/integrations/outbound-actions/{action_id}/priority",
            headers=_headers("company-a"),
            json={"priority": priority},
        ).status_code == 200

    summary = client.get("/api/integrations/operational-summary", headers=_headers("company-a"))
    assert summary.status_code == 200
    assert summary.json()["priority_counts"] == {
        "low": 1,
        "normal": 0,
        "high": 1,
        "urgent": 0,
    }


def test_operational_summary_counts_owned_and_unowned_actions_within_current_workspace(client):
    assert client.post("/api/workspaces", json={"slug": "company-a", "name": "Company A"}).status_code == 201
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "Company B"}).status_code == 201
    account_a = _account(client, "company-a", "generic_hmac")
    account_b = _account(client, "company-b", "generic_hmac")
    owned = _action(client, "company-a", account_a["id"], "owned")
    _action(client, "company-a", account_a["id"], "unowned")
    other_workspace = _action(client, "company-b", account_b["id"], "other-workspace")

    assert client.put(
        f"/api/integrations/outbound-actions/{owned['id']}/owner-reference",
        headers=_headers("company-a"),
        json={"owner_reference": "operator:42"},
    ).status_code == 200
    assert client.put(
        f"/api/integrations/outbound-actions/{other_workspace['id']}/owner-reference",
        headers=_headers("company-b"),
        json={"owner_reference": "operator:99"},
    ).status_code == 200

    summary = client.get("/api/integrations/operational-summary", headers=_headers("company-a"))
    assert summary.status_code == 200
    assert summary.json()["owned_outbound_action_count"] == 1
    assert summary.json()["unowned_outbound_action_count"] == 1
