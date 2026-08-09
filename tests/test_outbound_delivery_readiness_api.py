from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationDeliveryAttempt, OutboundIntegrationAction
from tests.test_outbound_delivery_approval_gate import _create_workspace_and_action, _headers


def _url(account: dict, action: dict) -> str:
    return f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/delivery-readiness"


def test_readiness_api_returns_safe_result_without_mutation(client):
    account, action = _create_workspace_and_action(client)
    response = client.get(_url(account, action), headers=_headers("company-a"))

    assert response.status_code == 200
    assert response.json()["action_id"] == action["id"]
    assert response.json()["status"] == "pending"
    assert response.json()["ready"] is False
    assert "approval_pending" in response.json()["blocking_reasons"]
    for field in ("content", "payload", "idempotency_key", "credential_hash", "secret_reference"):
        assert field not in response.json()

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        persisted = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert persisted is not None
        assert persisted.status == "pending"
        assert session.exec(select(OutboundIntegrationDeliveryAttempt)).all() == []


def test_readiness_api_is_workspace_scoped(client):
    account, action = _create_workspace_and_action(client)
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201
    assert client.get(_url(account, action), headers=_headers("company-b")).status_code == 404
