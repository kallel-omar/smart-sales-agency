from datetime import timedelta
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationDeliveryAttempt, utc_now


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _setup_failed_action(client) -> tuple[dict, dict]:
    assert client.post("/api/workspaces", json={"slug": "company-a", "name": "Company A"}).status_code == 201
    account = client.post(
        "/api/integrations/accounts",
        headers=_headers("company-a"),
        json={
            "provider": "unconfigured-provider",
            "external_account_id": "company-a",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    action = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_headers("company-a"),
        json={
            "external_target_id": "recipient",
            "action_type": "send_message",
            "content": "private",
            "idempotency_key": "attempt-filters",
        },
    ).json()
    deliver_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver"
    retry_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/retry"
    assert client.post(deliver_url, headers=_headers("company-a")).status_code == 200
    assert client.post(retry_url, headers=_headers("company-a")).status_code == 200
    return account, action


def test_attempt_history_supports_safe_filters_ordering_and_limits(client):
    account, action = _setup_failed_action(client)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        first = session.exec(
            select(OutboundIntegrationDeliveryAttempt).where(
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == UUID(action["id"]),
                OutboundIntegrationDeliveryAttempt.attempt_number == 1,
            )
        ).one()
        first.started_at = utc_now() - timedelta(days=1)
        session.add(first)
        session.commit()

    url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/delivery-attempts"
    response = client.get(url, headers=_headers("company-a"), params={"order": "newest_first", "limit": 1, "status": "failed"})
    assert response.status_code == 200
    assert [item["attempt_number"] for item in response.json()] == [2]
    assert client.get(
        url,
        headers=_headers("company-a"),
        params={"started_before": "2025-01-01T00:00:00Z"},
    ).json() == []


def test_attempt_history_filter_validation_and_workspace_scope_remain_safe(client):
    account, action = _setup_failed_action(client)
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "Company B"}).status_code == 201
    url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/delivery-attempts"
    invalid_range = client.get(
        url,
        headers=_headers("company-a"),
        params={"started_after": "2026-02-01T00:00:00Z", "started_before": "2026-01-01T00:00:00Z"},
    )
    assert invalid_range.status_code == 422
    assert invalid_range.json()["detail"] == "started_after must be earlier than or equal to started_before"
    assert client.get(url, headers=_headers("company-a"), params={"limit": 101}).status_code == 422
    cross_workspace = client.get(url, headers=_headers("company-b"))
    assert cross_workspace.status_code == 404
    assert cross_workspace.json()["detail"] == "Integration account not found"
