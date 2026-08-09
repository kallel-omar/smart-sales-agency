from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction, OutboundIntegrationAuditEvent, OutboundIntegrationDeliveryAttempt
from tests.test_outbound_action_audit import _headers, _setup


def _archive_url(action: dict) -> str:
    return f"/api/integrations/outbound-actions/{action['id']}/archive"


def _unarchive_url(action: dict) -> str:
    return f"/api/integrations/outbound-actions/{action['id']}/unarchive"


def test_terminal_action_can_be_archived_and_unarchived_without_delivery_mutation(client):
    account, action = _setup(client, "company-a")
    deliver_url = (
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver"
    )
    assert client.post(deliver_url, headers=_headers("company-a")).status_code == 200

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert stored is not None
        original = (stored.status, stored.delivered_at, stored.failed_at)
        attempts = session.exec(
            select(OutboundIntegrationDeliveryAttempt).where(
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == stored.id
            )
        ).all()
        assert len(attempts) == 1

    archived = client.post(_archive_url(action), headers=_headers("company-a"))
    assert archived.status_code == 200
    assert archived.json()["status"] == "delivered"
    assert archived.json()["archived_at"] is not None
    for sensitive in ("content", "payload", "idempotency_key", "secret_reference"):
        assert sensitive not in archived.json()
    assert client.get(
        "/api/integrations/outbound-actions", headers=_headers("company-a")
    ).json() == []
    assert client.post(_archive_url(action), headers=_headers("company-a")).status_code == 409

    restored = client.post(_unarchive_url(action), headers=_headers("company-a"))
    assert restored.status_code == 200
    assert restored.json()["status"] == "delivered"
    assert restored.json()["archived_at"] is None
    assert client.post(_unarchive_url(action), headers=_headers("company-a")).status_code == 409

    with next(session_dependency()) as session:
        stored = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert stored is not None
        assert (stored.status, stored.delivered_at, stored.failed_at) == original
        assert session.exec(
            select(OutboundIntegrationDeliveryAttempt).where(
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == stored.id
            )
        ).all() == attempts
        events = session.exec(
            select(OutboundIntegrationAuditEvent.action).where(
                OutboundIntegrationAuditEvent.outbound_integration_action_id == stored.id
            )
        ).all()
        assert [event.value for event in events] == [
            "created",
            "delivery_attempted",
            "delivered",
            "archived",
            "unarchived",
        ]


def test_archiving_requires_terminal_action_and_is_workspace_scoped(client):
    account, action = _setup(client, "company-a")
    assert client.post(_archive_url(action), headers=_headers("company-a")).status_code == 409
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201
    assert client.post(_archive_url(action), headers=_headers("company-b")).status_code == 404

    timeline = client.get(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/timeline",
        headers=_headers("company-a"),
    )
    assert timeline.status_code == 200
    assert "action_archived" not in [entry["event"] for entry in timeline.json()]
