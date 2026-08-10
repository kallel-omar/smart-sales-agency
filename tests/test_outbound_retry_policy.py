from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationDeliveryAttempt, Workspace
from app.services.delivery_adapters import DeliveryAdapterRegistry, DeliveryAdapterResult
from app.services.outbound_delivery import OutboundIntegrationDeliveryService
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def provision_account(client, workspace_slug: str, provider: str) -> dict:
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


def create_action(client, workspace_slug: str, account_id: str) -> dict:
    response = client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions",
        headers=workspace_headers(workspace_slug),
        json={
            "external_target_id": "recipient-123",
            "action_type": "send_message",
            "content": "Hello from Smart Sales Agency",
            "payload": {"format": "plain_text"},
            "correlation_id": "conversation-123",
            "idempotency_key": "retry-policy-message-1",
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


def attempt_numbers(client, workspace_slug: str, account_id: str, action_id: str) -> list[int]:
    response = client.get(
        f"/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/delivery-attempts",
        headers=workspace_headers(workspace_slug),
    )
    assert response.status_code == 200
    return [attempt["attempt_number"] for attempt in response.json()]


def test_failed_action_below_max_attempts_can_retry_by_default(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a", "unconfigured-provider")
    action = create_action(client, "company-a", account["id"])

    assert deliver(client, "company-a", account["id"], action["id"]).status_code == 200
    response = retry(client, "company-a", account["id"], action["id"])

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert attempt_numbers(client, "company-a", account["id"], action["id"]) == [1, 2]


def test_retry_is_denied_after_configured_maximum_without_a_new_attempt(client):
    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test",
        database_url="sqlite://",
        auth_token_secret="test-auth-token-secret-32-byte-value",
        outbound_delivery_max_attempts=2,
    )
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a", "unconfigured-provider")
    action = create_action(client, "company-a", account["id"])

    assert deliver(client, "company-a", account["id"], action["id"]).status_code == 200
    assert retry(client, "company-a", account["id"], action["id"]).status_code == 200
    denied = retry(client, "company-a", account["id"], action["id"])

    assert denied.status_code == 409
    assert denied.json()["detail"] == (
        "Outbound integration action retry is not eligible: maximum_attempts_reached"
    )
    assert attempt_numbers(client, "company-a", account["id"], action["id"]) == [1, 2]


def test_non_retryable_failure_code_is_denied_without_a_new_attempt(client):
    class NonRetryableAdapter:
        def deliver(self, action, account) -> DeliveryAdapterResult:
            del action, account
            return DeliveryAdapterResult.failure("invalid_recipient", "Recipient is invalid")

    create_workspace(client, "company-a")
    account = provision_account(client, "company-a", "test-provider")
    action = create_action(client, "company-a", account["id"])
    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        service = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry({"test-provider": NonRetryableAdapter()}),
            retry_policy=OutboundDeliveryRetryPolicy(3, {"invalid_recipient"}),
        )
        failed, _ = service.deliver_pending_action(
            workspace,
            UUID(account["id"]),
            UUID(action["id"]),
        )
        with pytest.raises(ValueError, match="failure_code_not_retryable"):
            service.retry_failed_action(workspace, UUID(account["id"]), UUID(action["id"]))

        attempts = session.exec(
            select(OutboundIntegrationDeliveryAttempt)
            .where(OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == failed.id)
            .order_by(OutboundIntegrationDeliveryAttempt.attempt_number)
        ).all()
        assert [attempt.attempt_number for attempt in attempts] == [1]


def test_retryable_failure_code_remains_allowed_when_not_denied(client):
    policy = OutboundDeliveryRetryPolicy(3, {"invalid_recipient"})

    decision = policy.evaluate(attempt_count=1, failure_code="temporary_failure")

    assert decision.allowed is True
    assert decision.denial_reason is None


def test_retry_policy_configuration_is_validated():
    with pytest.raises(ValidationError):
        Settings(outbound_delivery_max_attempts=0)
    with pytest.raises(ValidationError):
        Settings(outbound_delivery_non_retryable_failure_codes="invalid-code")
    with pytest.raises(ValueError, match="between 1 and 100"):
        OutboundDeliveryRetryPolicy(101)
