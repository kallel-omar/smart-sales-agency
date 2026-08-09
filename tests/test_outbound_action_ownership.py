from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _workspace(client, slug: str) -> None:
    assert client.post("/api/workspaces", json={"slug": slug, "name": slug}).status_code == 201


def _action(client, slug: str, key: str) -> dict:
    account = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={
            "provider": "generic_hmac",
            "external_account_id": f"{slug}-account",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    response = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_headers(slug),
        json={
            "external_target_id": key,
            "action_type": "send_message",
            "content": "private outbound content",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_outbound_action_owner_reference_is_optional_safe_and_does_not_change_delivery(client):
    _workspace(client, "company-a")
    action = _action(client, "company-a", "action-a")
    assert action["owner_reference"] is None

    assigned = client.put(
        f"/api/integrations/outbound-actions/{action['id']}/owner-reference",
        headers=_headers("company-a"),
        json={"owner_reference": "  operator:42  "},
    )
    assert assigned.status_code == 200
    assert assigned.json()["owner_reference"] == "operator:42"
    assert assigned.json()["status"] == "pending"
    for sensitive in ("content", "payload", "idempotency_key", "secret_reference"):
        assert sensitive not in assigned.json()

    detail = client.get(
        f"/api/integrations/outbound-actions/{action['id']}", headers=_headers("company-a")
    )
    assert detail.status_code == 200
    assert detail.json()["owner_reference"] == "operator:42"

    cleared = client.put(
        f"/api/integrations/outbound-actions/{action['id']}/owner-reference",
        headers=_headers("company-a"),
        json={"owner_reference": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["owner_reference"] is None

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert stored is not None
        assert stored.status.value == "pending"
        assert stored.owner_reference is None


def test_outbound_action_owner_reference_validates_and_is_workspace_scoped(client):
    _workspace(client, "company-a")
    _workspace(client, "company-b")
    action_a = _action(client, "company-a", "action-a")
    action_b = _action(client, "company-b", "action-b")

    invalid = client.put(
        f"/api/integrations/outbound-actions/{action_a['id']}/owner-reference",
        headers=_headers("company-a"),
        json={"owner_reference": "operator name"},
    )
    assert invalid.status_code == 422
    cross_workspace = client.put(
        f"/api/integrations/outbound-actions/{action_b['id']}/owner-reference",
        headers=_headers("company-a"),
        json={"owner_reference": "operator-1"},
    )
    assert cross_workspace.status_code == 404

    rows = client.get("/api/integrations/outbound-actions", headers=_headers("company-a"))
    assert rows.status_code == 200
    assert rows.json()[0]["owner_reference"] is None
