from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAuditEvent
from tests.test_outbound_action_audit import _headers, _setup


def _url(account: dict, action: dict) -> str:
    return (
        f"/api/integrations/accounts/{account['id']}/outbound-actions/"
        f"{action['id']}/transition-validation"
    )


def test_transition_validation_reuses_guard_without_mutation_or_audit(client):
    account, action = _setup(client, "company-a")

    allowed = client.get(
        _url(account, action), headers=_headers("company-a"), params={"target": "cancelled"}
    )
    denied = client.get(
        _url(account, action), headers=_headers("company-a"), params={"target": "pending"}
    )

    assert allowed.json() == {
        "allowed": True,
        "current_state": "pending",
        "requested_target": "cancelled",
        "denial_reason": None,
    }
    assert denied.json() == {
        "allowed": False,
        "current_state": "pending",
        "requested_target": "pending",
        "denial_reason": "transition_noop",
    }
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert len(session.exec(select(OutboundIntegrationAuditEvent)).all()) == 1


def test_transition_validation_is_workspace_scoped(client):
    account, action = _setup(client, "company-a")
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201

    response = client.get(
        _url(account, action), headers=_headers("company-b"), params={"target": "cancelled"}
    )

    assert response.status_code == 404
