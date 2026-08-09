from datetime import UTC, datetime, timedelta


def test_expired_pending_action_is_not_delivered_or_attempted(client):
    assert client.post("/api/workspaces", json={"slug":"company-a","name":"company-a"}).status_code == 201
    headers={"X-Workspace-Slug":"company-a"}
    account=client.post("/api/integrations/accounts",headers=headers,json={"provider":"generic_hmac","external_account_id":"a","secret_reference":"INTEGRATION_SECRET_GENERIC_HMAC_TEST"}).json()
    action=client.post(f"/api/integrations/accounts/{account['id']}/outbound-actions",headers=headers,json={"external_target_id":"r","action_type":"send_message","content":"p","idempotency_key":"expired","expires_at":(datetime.now(UTC)-timedelta(seconds=1)).isoformat()}).json()
    response=client.post(f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver",headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "Outbound integration action has expired"
    status=client.get(f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/delivery-status",headers=headers).json()
    assert status["status"] == "expired"
    assert status["attempt_count"] == 0


def test_action_without_expiration_keeps_existing_delivery_behavior(client):
    assert client.post("/api/workspaces", json={"slug":"company-a","name":"company-a"}).status_code == 201
    headers={"X-Workspace-Slug":"company-a"}
    account=client.post("/api/integrations/accounts",headers=headers,json={"provider":"generic_hmac","external_account_id":"a","secret_reference":"INTEGRATION_SECRET_GENERIC_HMAC_TEST"}).json()
    action=client.post(f"/api/integrations/accounts/{account['id']}/outbound-actions",headers=headers,json={"external_target_id":"r","action_type":"send_message","content":"p","idempotency_key":"active"}).json()
    assert client.post(f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver",headers=headers).status_code == 200
