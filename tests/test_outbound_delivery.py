from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
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


def create_action(client, workspace_slug: str, account_id: str, key: str = "message-1"):
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


def deliver_action(client, workspace_slug: str, account_id: str, action_id: str):
    return client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/deliver",
        headers=workspace_headers(workspace_slug),
    )


def test_pending_action_is_delivered_by_the_safe_default_adapter(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])

    response = deliver_action(client, "company-a", account["id"], action["id"])

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == action["id"]
    assert data["status"] == "delivered"
    assert data["provider_delivery_id"] == f"noop-{action['id']}"
    assert data["delivered_at"] is not None
    assert data["failed_at"] is None
    assert data["failure_code"] is None
    assert data["failure_message"] is None
    for excluded_field in (
        "payload",
        "idempotency_key",
        "credential_hash",
        "secret_reference",
    ):
        assert excluded_field not in data


def test_provider_specific_adapter_is_selected_and_safe_failure_is_persisted(client):
    class RecordingAdapter:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def deliver(self, action, account) -> DeliveryAdapterResult:
            self.calls.append((str(action.id), account.provider))
            return DeliveryAdapterResult.failure("recipient_unavailable", "Recipient unavailable")

    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    adapter = RecordingAdapter()
    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        workspace = session.exec(
            select(Workspace).where(Workspace.slug == "company-a")
        ).one()
        service = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry({"generic_hmac": adapter}),
        )
        processed, resolved_account = service.deliver_pending_action(
            workspace,
            UUID(account["id"]),
            UUID(action["id"]),
        )

        assert adapter.calls == [(action["id"], "generic_hmac")]
        assert resolved_account.id == UUID(account["id"])
        assert processed.status == OutboundIntegrationActionStatus.FAILED
        assert processed.failure_code == "recipient_unavailable"
        assert processed.failure_message == "Recipient unavailable"
        assert processed.failed_at is not None
        assert processed.delivered_at is None
        assert processed.provider_delivery_id is None


def test_unknown_provider_fails_closed_and_terminal_actions_are_not_delivered_twice(client):
    create_workspace(client, "company-a")
    unsupported = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers("company-a"),
        json={
            "provider": "unconfigured-provider",
            "external_account_id": "unsupported-account",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"] == "Unsupported integration provider"

    default_account = provision_account(client, "company-a")
    delivered_action = create_action(client, "company-a", default_account["id"], key="message-2")
    first_delivery = deliver_action(
        client,
        "company-a",
        default_account["id"],
        delivered_action["id"],
    )
    repeat_delivery = deliver_action(
        client,
        "company-a",
        default_account["id"],
        delivered_action["id"],
    )

    assert first_delivery.status_code == 200
    assert repeat_delivery.status_code == 409
    assert repeat_delivery.json()["detail"] == (
        "Outbound integration action has already reached a terminal state"
    )

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        persisted = session.get(OutboundIntegrationAction, UUID(delivered_action["id"]))
        assert persisted is not None
        assert persisted.status == OutboundIntegrationActionStatus.DELIVERED
        assert persisted.provider_delivery_id == f"noop-{delivered_action['id']}"


def test_delivery_is_scoped_to_the_current_workspace_and_account(client):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    company_b_account = provision_account(client, "company-b")
    company_b_action = create_action(client, "company-b", company_b_account["id"])

    response = deliver_action(
        client,
        "company-a",
        company_b_account["id"],
        company_b_action["id"],
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Integration account not found"
