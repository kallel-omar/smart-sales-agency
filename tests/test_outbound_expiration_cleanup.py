from datetime import UTC, datetime, timedelta


def test_expiration_cleanup_is_explicit_workspace_scoped_and_safe(client):
    for slug in ("company-a", "company-b"):
        assert client.post("/api/workspaces", json={"slug":slug,"name":slug}).status_code == 201
    def create(slug, key):
        h={"X-Workspace-Slug":slug}
        account=client.post("/api/integrations/accounts",headers=h,json={"provider":"generic_hmac","external_account_id":key,"secret_reference":"INTEGRATION_SECRET_GENERIC_HMAC_TEST"}).json()
        action=client.post(f"/api/integrations/accounts/{account['id']}/outbound-actions",headers=h,json={"external_target_id":"r","action_type":"send_message","content":"p","idempotency_key":key,"expires_at":(datetime.now(UTC)-timedelta(seconds=1)).isoformat()}).json()
        assert client.post(f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver",headers=h).status_code == 409
        return action
    a=create("company-a","a")
    b=create("company-b","b")
    result=client.post("/api/integrations/outbound-actions/expiration-cleanup",headers={"X-Workspace-Slug":"company-a"})
    assert result.status_code == 200 and result.json()["deleted_count"] == 1
    assert client.get(f"/api/integrations/outbound-actions/{a['id']}",headers={"X-Workspace-Slug":"company-a"}).status_code == 404
    assert client.get(f"/api/integrations/outbound-actions/{b['id']}",headers={"X-Workspace-Slug":"company-b"}).status_code == 200
