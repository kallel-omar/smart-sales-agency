from uuid import UUID

from sqlmodel import select

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction, OutboundIntegrationDeliveryAttempt


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def provision_account(client, workspace_slug: str, provider: str = "generic_hmac") -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(workspace_slug),
        json={
            "provider": provider,
            "external_account_id": f"{workspace_slug}-{provider}",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    return response.json()


def create_action(client, workspace_slug: str, account_id: str, key: str = "status-message-1") -> dict:
    response = client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions",
        headers=workspace_headers(workspace_slug),
        json={
            "external_target_id": "recipient-123",
            "action_type": "send_message",
            "content": "Hello from Smart Sales Agency",
            "payload": {"format": "plain_text"},
            "correlation_id": "conversation-123",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()


def deliver(client, workspace_slug: str, account_id: str, action_id: str):
    return client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/deliver",
        headers=workspace_headers(workspace_slug),
    )


def retry(client, workspace_slug: str, account_id: str, action_id: str):
    return client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/retry",
        headers=workspace_headers(workspace_slug),
    )


def delivery_status(client, workspace_slug: str, account_id: str, action_id: str):
    return client.get(
        f"/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/delivery-status",
        headers=workspace_headers(workspace_slug),
    )


def test_pending_status_is_safe_read_only_and_has_no_retry_eligibility(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        before = session.exec(select(OutboundIntegrationDeliveryAttempt)).all()
        assert before == []

    response = delivery_status(client, "company-a", account["id"], action["id"])

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == action["id"]
    assert data["provider"] == "generic_hmac"
    assert data["external_target_id"] == "recipient-123"
    assert data["status"] == "pending"
    assert data["attempt_count"] == 0
    assert data["retry_allowed"] is False
    assert data["retry_denial_reason"] == "action_not_failed"
    for sensitive_field in (
        "content",
        "payload",
        "correlation_id",
        "idempotency_key",
        "credential_hash",
        "secret_reference",
        "secret_value",
    ):
        assert sensitive_field not in data

    with next(session_dependency()) as session:
        persisted = session.get(OutboundIntegrationAction, UUID(action["id"]))
        attempts = session.exec(select(OutboundIntegrationDeliveryAttempt)).all()
        assert persisted is not None
        assert persisted.status.value == "pending"
        assert attempts == []


def test_delivered_status_is_terminal_with_the_correct_attempt_count(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])

    assert deliver(client, "company-a", account["id"], action["id"]).status_code == 200
    response = delivery_status(client, "company-a", account["id"], action["id"])

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "delivered"
    assert data["attempt_count"] == 1
    assert data["provider_delivery_id"] == f"noop-{action['id']}"
    assert data["delivered_at"] is not None
    assert data["failed_at"] is None
    assert data["retry_allowed"] is False
    assert data["retry_denial_reason"] == "action_delivered"


def test_failed_status_reports_retry_allowed_under_the_configured_maximum(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a", provider="unconfigured-provider")
    action = create_action(client, "company-a", account["id"])

    assert deliver(client, "company-a", account["id"], action["id"]).status_code == 200
    response = delivery_status(client, "company-a", account["id"], action["id"])

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["failure_code"] == "adapter_not_configured"
    assert data["attempt_count"] == 1
    assert data["retry_allowed"] is True
    assert data["retry_denial_reason"] is None


def test_failed_status_denies_retry_after_the_configured_maximum(client):
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test",
        database_url="sqlite://",
        outbound_delivery_max_attempts=2,
    )
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a", provider="unconfigured-provider")
    action = create_action(client, "company-a", account["id"])

    assert deliver(client, "company-a", account["id"], action["id"]).status_code == 200
    assert retry(client, "company-a", account["id"], action["id"]).status_code == 200
    response = delivery_status(client, "company-a", account["id"], action["id"])

    assert response.status_code == 200
    assert response.json()["attempt_count"] == 2
    assert response.json()["retry_allowed"] is False
    assert response.json()["retry_denial_reason"] == "maximum_attempts_reached"


def test_failed_status_denies_a_configured_non_retryable_failure_code(client):
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test",
        database_url="sqlite://",
        outbound_delivery_non_retryable_failure_codes="adapter_not_configured",
    )
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a", provider="unconfigured-provider")
    action = create_action(client, "company-a", account["id"])

    assert deliver(client, "company-a", account["id"], action["id"]).status_code == 200
    response = delivery_status(client, "company-a", account["id"], action["id"])

    assert response.status_code == 200
    assert response.json()["retry_allowed"] is False
    assert response.json()["retry_denial_reason"] == "failure_code_not_retryable"


def test_delivery_status_is_scoped_to_the_current_workspace(client):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    account = provision_account(client, "company-b")
    action = create_action(client, "company-b", account["id"])

    response = delivery_status(client, "company-a", account["id"], action["id"])

    assert response.status_code == 404
    assert response.json()["detail"] == "Integration account not found"
