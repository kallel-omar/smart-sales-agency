from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction, OutboundIntegrationActionStatus


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def provision_account(client, workspace_slug: str, *, active: bool = True) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(workspace_slug),
        json={
            "provider": "generic_hmac",
            "external_account_id": f"{workspace_slug}-account",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    account = response.json()
    if not active:
        response = client.post(
            f"/api/integrations/accounts/{account['id']}/deactivate",
            headers=workspace_headers(workspace_slug),
        )
        assert response.status_code == 200
    return account


def action_payload(**overrides) -> dict:
    payload = {
        "external_target_id": "recipient-123",
        "action_type": "send_message",
        "content": "Hello from Smart Sales Agency",
        "payload": {"format": "plain_text"},
        "correlation_id": "conversation-123",
        "idempotency_key": "message-123",
    }
    payload.update(overrides)
    return payload


def create_action(client, workspace_slug: str, account_id: str, **overrides):
    return client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions",
        headers=workspace_headers(workspace_slug),
        json=action_payload(**overrides),
    )


def test_valid_outbound_action_is_persisted_with_a_safe_provider_neutral_response(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")

    response = create_action(client, "company-a", account["id"])

    assert response.status_code == 201
    data = response.json()
    assert data["workspace_id"] == account["workspace_id"]
    assert data["integration_account_id"] == account["id"]
    assert data["provider"] == "generic_hmac"
    assert data["external_target_id"] == "recipient-123"
    assert data["action_type"] == "send_message"
    assert data["content"] == "Hello from Smart Sales Agency"
    assert data["correlation_id"] == "conversation-123"
    assert data["status"] == "pending"
    for excluded_field in ("payload", "idempotency_key", "credential_hash", "secret_reference"):
        assert excluded_field not in data

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        action = session.get(OutboundIntegrationAction, UUID(data["id"]))
        assert action is not None
        assert action.payload == {"format": "plain_text"}
        assert action.status == OutboundIntegrationActionStatus.PENDING


def test_invalid_action_and_workspace_override_are_rejected(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")

    invalid = create_action(
        client,
        "company-a",
        account["id"],
        action_type="unsupported_action",
    )
    assert invalid.status_code == 422

    workspace_override = create_action(
        client,
        "company-a",
        account["id"],
        workspace_id="not-accepted",
    )
    assert workspace_override.status_code == 422


def test_outbound_actions_require_an_active_account_in_the_current_workspace(client):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    active_account = provision_account(client, "company-a")
    inactive_account = provision_account(client, "company-a", active=False)
    company_b_account = provision_account(client, "company-b")

    inactive = create_action(client, "company-a", inactive_account["id"])
    assert inactive.status_code == 409
    assert inactive.json()["detail"] == "Integration account is inactive"

    cross_workspace = create_action(client, "company-a", company_b_account["id"])
    assert cross_workspace.status_code == 404
    assert cross_workspace.json()["detail"] == "Integration account not found"

    unknown = create_action(client, "company-a", "00000000-0000-0000-0000-000000000000")
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Integration account not found"

    accepted = create_action(client, "company-a", active_account["id"])
    assert accepted.status_code == 201


def test_idempotency_returns_the_original_action_and_rejects_key_reuse_with_changes(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")

    first = create_action(client, "company-a", account["id"])
    repeated = create_action(client, "company-a", account["id"])

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json() == first.json()

    conflict = create_action(
        client,
        "company-a",
        account["id"],
        content="A different message",
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == (
        "Idempotency key has already been used for a different action"
    )

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        actions = session.exec(select(OutboundIntegrationAction)).all()
        assert len(actions) == 1
