from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    OutboundDeliveryFailureClassification,
    OutboundIntegrationAction,
    OutboundIntegrationDeliveryAttempt,
    Workspace,
)
from app.services.delivery_adapters import (
    DeliveryAdapterRegistry,
    DeliveryAdapterResult,
    classify_failure_code,
)
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


def _create_action(client, provider: str) -> tuple[dict, dict]:
    assert client.post("/api/workspaces", json={"slug": "company-a", "name": "Company A"}).status_code == 201
    headers = {"X-Workspace-Slug": "company-a"}
    account = client.post(
        "/api/integrations/accounts",
        headers=headers,
        json={
            "provider": provider,
            "external_account_id": provider,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    action = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=headers,
        json={
            "external_target_id": "recipient",
            "action_type": "send_message",
            "content": "private",
            "idempotency_key": "classification",
        },
    ).json()
    return account, action


def test_generic_safe_failure_codes_have_provider_neutral_classifications():
    assert classify_failure_code("temporary_failure") == OutboundDeliveryFailureClassification.TEMPORARY
    assert classify_failure_code("adapter_not_configured") == OutboundDeliveryFailureClassification.PERMANENT
    assert classify_failure_code("unknown_code") == OutboundDeliveryFailureClassification.UNKNOWN


def test_adapter_classification_is_persisted_on_action_and_attempt(client):
    class FailingAdapter:
        def deliver(self, action, account):
            del action, account
            return DeliveryAdapterResult.failure(
                "provider_safe_code",
                "Safe failure",
                OutboundDeliveryFailureClassification.RATE_LIMIT,
            )

    account, action = _create_action(client, "classification-provider")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        delivered_action, _ = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry({"classification-provider": FailingAdapter()}),
        ).deliver_pending_action(workspace, UUID(account["id"]), UUID(action["id"]))
        attempt = session.exec(
            select(OutboundIntegrationDeliveryAttempt).where(
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == delivered_action.id
            )
        ).one()
        assert delivered_action.failure_classification == OutboundDeliveryFailureClassification.RATE_LIMIT
        assert attempt.failure_classification == OutboundDeliveryFailureClassification.RATE_LIMIT


def test_unknown_adapter_failure_is_classified_and_persisted(client):
    account, action = _create_action(client, "missing-provider")
    response = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver",
        headers={"X-Workspace-Slug": "company-a"},
    )
    assert response.status_code == 200
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        stored = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert stored is not None
        assert stored.failure_classification == OutboundDeliveryFailureClassification.PERMANENT
