from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    OutboundDeliveryFailureClassification,
    OutboundIntegrationActionStatus,
    OutboundIntegrationActionType,
    OutboundIntegrationDeliveryAttempt,
    Workspace,
)
from app.services.delivery_adapters import (
    DeliveryAdapterCapabilities,
    DeliveryAdapterRegistry,
    DeliveryAdapterResult,
)
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


def _setup(client, provider: str, content: str = "hello") -> tuple[dict, dict]:
    assert (
        client.post(
            "/api/workspaces",
            json={"slug": "company-a", "name": "Company A"},
        ).status_code
        == 201
    )
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
            "content": content,
            "idempotency_key": provider,
        },
    ).json()
    return account, action


class RecordingAdapter:
    capabilities = DeliveryAdapterCapabilities(
        supported_action_types=frozenset({OutboundIntegrationActionType.SEND_MESSAGE}),
        max_content_length=5,
    )

    def __init__(self) -> None:
        self.call_count = 0

    def deliver(self, action, account) -> DeliveryAdapterResult:
        del action, account
        self.call_count += 1
        return DeliveryAdapterResult.success("provider-id")


def _deliver_with_recording_adapter(client, account: dict, action: dict, adapter: RecordingAdapter):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        processed, _ = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry({account["provider"]: adapter}),
        ).deliver_pending_action(workspace, UUID(account["id"]), UUID(action["id"]))
        attempt = session.exec(
            select(OutboundIntegrationDeliveryAttempt).where(
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id == processed.id
            )
        ).one()
        return processed, attempt


def test_supported_action_is_delivered_after_capability_validation(client):
    account, action = _setup(client, "capability-provider", content="hello")
    adapter = RecordingAdapter()

    processed, _ = _deliver_with_recording_adapter(client, account, action, adapter)

    assert processed.status == OutboundIntegrationActionStatus.DELIVERED
    assert adapter.call_count == 1


def test_unsupported_action_type_fails_without_invoking_adapter(client):
    account, _ = _setup(client, "capability-provider", content="hello")
    unsupported = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers={"X-Workspace-Slug": "company-a"},
        json={
            "external_target_id": "recipient-media",
            "action_type": "send_media",
            "content": "hello",
            "idempotency_key": "media",
        },
    )
    assert unsupported.status_code == 201

    adapter = RecordingAdapter()
    processed, attempt = _deliver_with_recording_adapter(
        client, account, unsupported.json(), adapter
    )

    assert adapter.call_count == 0
    assert processed.status == OutboundIntegrationActionStatus.FAILED
    assert processed.failure_code == "unsupported_action_type"
    assert processed.failure_classification == OutboundDeliveryFailureClassification.VALIDATION
    assert attempt.failure_code == "unsupported_action_type"


def test_content_limit_failure_is_persisted_without_invoking_adapter(client):
    account, action = _setup(client, "capability-provider", content="too long")
    adapter = RecordingAdapter()

    processed, attempt = _deliver_with_recording_adapter(client, account, action, adapter)

    assert adapter.call_count == 0
    assert processed.status == OutboundIntegrationActionStatus.FAILED
    assert processed.failure_code == "content_too_long"
    assert processed.failure_classification == OutboundDeliveryFailureClassification.VALIDATION
    assert attempt.status == OutboundIntegrationActionStatus.FAILED
