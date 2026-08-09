from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAuditEvent


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _setup(client, slug: str, provider: str = "generic_hmac", *, expired: bool = False):
    assert client.post("/api/workspaces", json={"slug": slug, "name": slug}).status_code == 201
    account = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={"provider": provider, "external_account_id": slug, "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST"},
    ).json()
    body = {"external_target_id": "recipient", "action_type": "send_message", "content": "not audited", "idempotency_key": slug}
    if expired:
        body["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    action = client.post(f"/api/integrations/accounts/{account['id']}/outbound-actions", headers=_headers(slug), json=body).json()
    return account, action


def _events_for_action() -> list[OutboundIntegrationAuditEvent]:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        return session.exec(select(OutboundIntegrationAuditEvent).order_by(OutboundIntegrationAuditEvent.created_at)).all()


def test_outbound_lifecycle_transitions_record_safe_audit_events(client):
    account, action = _setup(client, "company-a")
    action_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(f"{action_url}/deliver", headers=_headers("company-a")).status_code == 200
    assert [event.action.value for event in _events_for_action()] == ["created", "delivery_attempted", "delivered"]
    event = _events_for_action()[0]
    assert event.workspace_id == UUID(account["workspace_id"])
    assert event.integration_account_id == UUID(account["id"])
    assert event.outbound_integration_action_id == UUID(action["id"])
    for sensitive in ("content", "payload", "idempotency_key", "credential_hash", "secret_reference"):
        assert not hasattr(event, sensitive)


def test_failed_retry_cancel_and_expiration_audit_events_are_accurate(client):
    account, action = _setup(client, "failed", "missing-provider")
    url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(f"{url}/deliver", headers=_headers("failed")).status_code == 200
    assert client.post(f"{url}/retry", headers=_headers("failed")).status_code == 200
    cancelled_account, cancelled = _setup(client, "cancelled")
    assert client.post(f"/api/integrations/accounts/{cancelled_account['id']}/outbound-actions/{cancelled['id']}/cancel", headers=_headers("cancelled")).status_code == 200
    expired_account, expired = _setup(client, "expired", expired=True)
    assert client.post(f"/api/integrations/accounts/{expired_account['id']}/outbound-actions/{expired['id']}/deliver", headers=_headers("expired")).status_code == 409
    assert [event.action.value for event in _events_for_action()] == [
        "created", "delivery_attempted", "failed", "retried", "delivery_attempted", "failed",
        "created", "cancelled", "created", "expired",
    ]


def test_denied_and_cross_workspace_operations_do_not_create_audit_events(client):
    account, action = _setup(client, "company-a")
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201
    url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(f"{url}/cancel", headers=_headers("company-b")).status_code == 404
    assert client.post(f"{url}/cancel", headers=_headers("company-a")).status_code == 200
    assert client.post(f"{url}/cancel", headers=_headers("company-a")).status_code == 409
    assert [event.action.value for event in _events_for_action()] == ["created", "cancelled"]
