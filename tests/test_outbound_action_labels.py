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


def test_outbound_action_labels_are_normalized_ordered_and_do_not_change_delivery_state(client):
    _workspace(client, "company-a")
    action = _action(client, "company-a", "action-a")

    first = client.post(
        f"/api/integrations/outbound-actions/{action['id']}/labels",
        headers=_headers("company-a"),
        json={"label": "  Follow_Up  "},
    )
    second = client.post(
        f"/api/integrations/outbound-actions/{action['id']}/labels",
        headers=_headers("company-a"),
        json={"label": "urgent"},
    )
    repeated = client.post(
        f"/api/integrations/outbound-actions/{action['id']}/labels",
        headers=_headers("company-a"),
        json={"label": "follow_up"},
    )

    assert first.status_code == second.status_code == repeated.status_code == 201
    assert first.json()["label"] == repeated.json()["label"] == "follow_up"
    listed = client.get(
        f"/api/integrations/outbound-actions/{action['id']}/labels",
        headers=_headers("company-a"),
    )
    assert listed.status_code == 200
    assert [item["label"] for item in listed.json()] == ["follow_up", "urgent"]
    assert all(set(item) == {"label", "created_at"} for item in listed.json())

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored = session.exec(select(OutboundIntegrationAction)).one()
        assert stored.status.value == "pending"


def test_outbound_action_labels_validate_remove_and_enforce_workspace_isolation(client):
    _workspace(client, "company-a")
    _workspace(client, "company-b")
    action_a = _action(client, "company-a", "action-a")
    action_b = _action(client, "company-b", "action-b")

    invalid = client.post(
        f"/api/integrations/outbound-actions/{action_a['id']}/labels",
        headers=_headers("company-a"),
        json={"label": "not safe"},
    )
    assert invalid.status_code == 422

    added = client.post(
        f"/api/integrations/outbound-actions/{action_a['id']}/labels",
        headers=_headers("company-a"),
        json={"label": "review"},
    )
    assert added.status_code == 201
    assert client.delete(
        f"/api/integrations/outbound-actions/{action_a['id']}/labels/review",
        headers=_headers("company-a"),
    ).status_code == 204
    assert client.get(
        f"/api/integrations/outbound-actions/{action_a['id']}/labels",
        headers=_headers("company-a"),
    ).json() == []
    assert client.get(
        f"/api/integrations/outbound-actions/{action_b['id']}/labels",
        headers=_headers("company-a"),
    ).status_code == 404
