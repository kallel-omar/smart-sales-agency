from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _create_workspace(client, slug: str) -> None:
    assert client.post("/api/workspaces", json={"slug": slug, "name": slug}).status_code == 201


def _create_action(client, slug: str) -> dict:
    account = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={
            "provider": "generic_hmac",
            "external_account_id": slug,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    response = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_headers(slug),
        json={
            "external_target_id": "recipient-safe-id",
            "action_type": "send_message",
            "content": "Private message content",
            "payload": {"private": "payload"},
            "correlation_id": "private-correlation",
            "idempotency_key": f"detail-{slug}",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_detail_view_is_safe_workspace_scoped_and_read_only(client):
    _create_workspace(client, "company-a")
    action = _create_action(client, "company-a")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        before = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert before is not None
        before_created_at = before.created_at

    response = client.get(
        f"/api/integrations/outbound-actions/{action['id']}",
        headers=_headers("company-a"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == action["id"]
    assert data["integration_account_id"] == action["integration_account_id"]
    assert data["provider"] == "generic_hmac"
    assert data["external_target_id"] == "recipient-safe-id"
    assert data["status"] == "pending"
    assert data["failure_message"] is None
    for field in ("content", "payload", "correlation_id", "idempotency_key", "workspace_id", "secret_reference"):
        assert field not in data
    with next(session_dependency()) as session:
        after = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert after is not None
        assert after.created_at == before_created_at
        assert session.exec(select(OutboundIntegrationAction)).all() == [after]


def test_detail_view_does_not_cross_workspace_boundaries(client):
    _create_workspace(client, "company-a")
    _create_workspace(client, "company-b")
    action = _create_action(client, "company-b")

    response = client.get(
        f"/api/integrations/outbound-actions/{action['id']}",
        headers=_headers("company-a"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Outbound integration action not found"
