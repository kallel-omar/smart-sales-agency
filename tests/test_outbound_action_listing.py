from datetime import timedelta
from uuid import UUID

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction, utc_now


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _workspace(client, slug: str) -> None:
    assert client.post("/api/workspaces", json={"slug": slug, "name": slug}).status_code == 201


def _account(client, slug: str, provider: str = "generic_hmac") -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={
            "provider": provider,
            "external_account_id": f"{slug}-{provider}",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    return response.json()


def _action(client, slug: str, account_id: str, key: str) -> dict:
    response = client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions",
        headers=_headers(slug),
        json={
            "external_target_id": key,
            "action_type": "send_message",
            "content": "private outbound content",
            "payload": {"private": True},
            "correlation_id": "private-correlation",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_workspace_action_listing_filters_orders_and_omits_sensitive_fields(client):
    _workspace(client, "company-a")
    account = _account(client, "company-a")
    first = _action(client, "company-a", account["id"], "first")
    second = _action(client, "company-a", account["id"], "second")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored_first = session.get(OutboundIntegrationAction, UUID(first["id"]))
        assert stored_first is not None
        stored_first.created_at = utc_now() - timedelta(days=1)
        session.add(stored_first)
        session.commit()

    response = client.get("/api/integrations/outbound-actions", headers=_headers("company-a"))

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [second["id"], first["id"]]
    listed = response.json()[0]
    assert listed["provider"] == "generic_hmac"
    for field in ("content", "payload", "correlation_id", "idempotency_key", "workspace_id", "secret_reference"):
        assert field not in listed


def test_workspace_action_listing_supports_safe_filters_and_isolated_results(client):
    _workspace(client, "company-a")
    _workspace(client, "company-b")
    generic = _account(client, "company-a")
    other = _account(client, "company-a", "generic_webhook")
    _action(client, "company-a", generic["id"], "generic")
    other_action = _action(client, "company-a", other["id"], "other")
    _action(client, "company-b", _account(client, "company-b")["id"], "other-workspace")

    filtered = client.get(
        "/api/integrations/outbound-actions",
        headers=_headers("company-a"),
        params={"provider": "generic_webhook", "integration_account_id": other["id"], "limit": 1},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [other_action["id"]]
    assert client.get(
        "/api/integrations/outbound-actions",
        headers=_headers("company-a"),
        params={"created_after": "2030-01-01T00:00:00Z"},
    ).json() == []


def test_workspace_action_listing_supports_exact_priority_filter(client):
    _workspace(client, "company-a")
    account = _account(client, "company-a")
    normal = _action(client, "company-a", account["id"], "normal")
    high = _action(client, "company-a", account["id"], "high")

    assert client.put(
        f"/api/integrations/outbound-actions/{high['id']}/priority",
        headers=_headers("company-a"),
        json={"priority": "high"},
    ).status_code == 200

    filtered = client.get(
        "/api/integrations/outbound-actions",
        headers=_headers("company-a"),
        params={"priority": "high"},
    )

    assert filtered.status_code == 200
    assert [(item["id"], item["priority"]) for item in filtered.json()] == [
        (high["id"], "high")
    ]
    assert normal["id"] not in [item["id"] for item in filtered.json()]


def test_workspace_action_listing_rejects_invalid_ranges_and_limits(client):
    _workspace(client, "company-a")
    response = client.get(
        "/api/integrations/outbound-actions",
        headers=_headers("company-a"),
        params={"created_after": "2026-02-01T00:00:00Z", "created_before": "2026-01-01T00:00:00Z"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "created_after must be earlier than or equal to created_before"
    assert client.get(
        "/api/integrations/outbound-actions", headers=_headers("company-a"), params={"limit": 101}
    ).status_code == 422
