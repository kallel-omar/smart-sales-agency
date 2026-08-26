from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationDeliveryAttempt,
    Workspace,
)
from app.services.delivery_adapters import DeliveryAdapterRegistry, DeliveryAdapterResult
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


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


def create_action(client, workspace_slug: str, account_id: str, key: str = "message-1") -> dict:
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


def attempts(client, workspace_slug: str, account_id: str, action_id: str):
    return client.get(
        f"/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/delivery-attempts",
        headers=workspace_headers(workspace_slug),
    )


def test_first_delivery_creates_safe_attempt_one(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])

    assert deliver(client, "company-a", account["id"], action["id"]).status_code == 200
    response = attempts(client, "company-a", account["id"], action["id"])

    assert response.status_code == 200
    attempt_data = response.json()[0]
    assert response.json() == [
        {
            "id": attempt_data["id"],
            "workspace_id": account["workspace_id"],
            "integration_account_id": account["id"],
            "outbound_integration_action_id": action["id"],
            "attempt_number": 1,
            "status": "delivered",
            "provider_delivery_id": f"noop-{action['id']}",
            "started_at": attempt_data["started_at"],
            "completed_at": attempt_data["completed_at"],
            "failure_code": None,
            "failure_message": None,
        }
    ]
    assert attempt_data["completed_at"] is not None
    for sensitive_field in (
        "content",
        "payload",
        "idempotency_key",
        "credential_hash",
        "secret_reference",
        "secret_value",
    ):
        assert sensitive_field not in attempt_data


def test_failed_retries_use_the_same_action_and_increase_attempt_numbers(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a", provider="generic_webhook")
    action = create_action(client, "company-a", account["id"])

    first = deliver(client, "company-a", account["id"], action["id"])
    second = retry(client, "company-a", account["id"], action["id"])
    third = retry(client, "company-a", account["id"], action["id"])

    assert [response.status_code for response in (first, second, third)] == [200, 200, 200]
    assert [response.json()["id"] for response in (first, second, third)] == [action["id"]] * 3
    assert [response.json()["status"] for response in (first, second, third)] == ["failed"] * 3
    history = attempts(client, "company-a", account["id"], action["id"])
    assert history.status_code == 200
    assert [attempt["attempt_number"] for attempt in history.json()] == [1, 2, 3]


def test_successful_explicit_retry_marks_the_original_action_delivered(client):
    class FlakyAdapter:
        def __init__(self) -> None:
            self.results = [
                DeliveryAdapterResult.failure("temporary_failure", "Try again"),
                DeliveryAdapterResult.success("provider-message-2"),
            ]

        def deliver(self, action, account) -> DeliveryAdapterResult:
            del action, account
            return self.results.pop(0)

    create_workspace(client, "company-a")
    account = provision_account(client, "company-a", provider="generic_hmac")
    action = create_action(client, "company-a", account["id"])
    session_dependency = app.dependency_overrides[get_session]
    adapter = FlakyAdapter()

    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        service = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry({"generic_hmac": adapter}),
        )
        failed, _ = service.deliver_pending_action(
            workspace,
            UUID(account["id"]),
            UUID(action["id"]),
        )
        delivered, _ = service.retry_failed_action(
            workspace,
            UUID(account["id"]),
            UUID(action["id"]),
        )

        assert failed.id == delivered.id == UUID(action["id"])
        assert delivered.status == OutboundIntegrationActionStatus.DELIVERED
        assert delivered.provider_delivery_id == "provider-message-2"
        persisted_attempts = session.exec(
            select(OutboundIntegrationDeliveryAttempt)
            .where(OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == delivered.id)
            .order_by(OutboundIntegrationDeliveryAttempt.attempt_number)
        ).all()
        assert [(attempt.attempt_number, attempt.status) for attempt in persisted_attempts] == [
            (1, OutboundIntegrationActionStatus.FAILED),
            (2, OutboundIntegrationActionStatus.DELIVERED),
        ]

    delivered_retry = retry(client, "company-a", account["id"], action["id"])
    assert delivered_retry.status_code == 409
    assert delivered_retry.json()["detail"] == "Only failed outbound integration actions can be retried"


def test_attempt_history_and_retry_are_workspace_scoped(client):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    company_b_account = provision_account(client, "company-b", provider="generic_webhook")
    company_b_action = create_action(client, "company-b", company_b_account["id"])

    assert deliver(client, "company-b", company_b_account["id"], company_b_action["id"]).status_code == 200
    cross_workspace_retry = retry(
        client,
        "company-a",
        company_b_account["id"],
        company_b_action["id"],
    )
    cross_workspace_history = attempts(
        client,
        "company-a",
        company_b_account["id"],
        company_b_action["id"],
    )

    assert cross_workspace_retry.status_code == 404
    assert cross_workspace_history.status_code == 404
    assert cross_workspace_retry.json()["detail"] == "Integration account not found"
    assert cross_workspace_history.json()["detail"] == "Integration account not found"

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        action = session.get(OutboundIntegrationAction, UUID(company_b_action["id"]))
        assert action is not None
        assert action.status == OutboundIntegrationActionStatus.FAILED
