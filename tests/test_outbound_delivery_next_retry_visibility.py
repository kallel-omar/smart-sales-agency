from datetime import timedelta
from uuid import UUID

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction, utc_now


def test_failed_status_exposes_future_retry_time_without_mutating_action(client):
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test",
        database_url="sqlite://",
        auth_token_secret="test-auth-token-secret-32-byte-value",
        outbound_delivery_retry_delay_seconds=300,
        outbound_delivery_retry_delay_max_seconds=300,
    )
    assert client.post("/api/workspaces", json={"slug": "company-a", "name": "Company A"}).status_code == 201
    headers = {"X-Workspace-Slug": "company-a"}
    account = client.post(
        "/api/integrations/accounts",
        headers=headers,
        json={
            "provider": "missing-provider",
            "external_account_id": "company-a",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    action = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=headers,
        json={
            "external_target_id": "recipient",
            "action_type": "send_message",
            "content": "private",
            "idempotency_key": "next-retry",
        },
    ).json()
    deliver_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver"
    assert client.post(deliver_url, headers=headers).status_code == 200
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert stored is not None
        stored.failed_at = utc_now() - timedelta(seconds=1)
        session.add(stored)
        session.commit()

    response = client.get(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/delivery-status",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["retry_allowed"] is False
    assert data["retry_denial_reason"] == "retry_delay_not_elapsed"
    assert data["next_retry_at"] is not None
    with next(session_dependency()) as session:
        stored = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert stored is not None
        assert stored.status.value == "failed"
