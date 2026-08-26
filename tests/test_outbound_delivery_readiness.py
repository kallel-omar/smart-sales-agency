from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction, Workspace
from app.services.delivery_adapters import (
    DeliveryAdapterRegistry,
    DeliveryAdapterResult,
    NoopDeliveryAdapter,
)
from app.services.outbound_delivery import OutboundIntegrationDeliveryService
from app.services.outbound_delivery_readiness import OutboundDeliveryReadinessService
from app.services.outbound_retry_delay_policy import OutboundDeliveryRetryDelayPolicy
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy
from tests.test_outbound_delivery_approval_gate import _create_workspace_and_action, _headers


def _service(session, registry=None):
    return OutboundDeliveryReadinessService(
        session,
        retry_policy=OutboundDeliveryRetryPolicy(3),
        retry_delay_policy=OutboundDeliveryRetryDelayPolicy("fixed", 0, 0),
        adapter_registry=registry,
    )


def test_readiness_composes_approval_not_before_and_capability_constraints(client):
    account, action = _create_workspace_and_action(client)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        registry = DeliveryAdapterRegistry({"generic_hmac": NoopDeliveryAdapter()})
        view = _service(session, registry).evaluate(workspace, UUID(account["id"]), UUID(action["id"]))
        assert view.ready is False
        assert view.blocking_reasons == ("approval_pending",)

        persisted = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert persisted is not None
        persisted.requires_approval = False
        persisted.not_before = datetime.now(UTC) + timedelta(minutes=5)
        session.add(persisted)
        session.commit()
        view = _service(session, registry).evaluate(workspace, UUID(account["id"]), UUID(action["id"]))
        assert view.blocking_reasons == ("not_before_not_reached",)

        persisted.not_before = None
        persisted.action_type = "send_media"
        session.add(persisted)
        session.commit()
        view = _service(session, registry).evaluate(workspace, UUID(account["id"]), UUID(action["id"]))
        assert view.blocking_reasons == ("adapter_capability_mismatch",)


def test_readiness_uses_existing_retry_policy_and_terminal_state(client):
    assert client.post("/api/workspaces", json={"slug": "company-a", "name": "company-a"}).status_code == 201
    account = client.post(
        "/api/integrations/accounts", headers=_headers("company-a"),
        json={"provider": "generic_hmac", "external_account_id": "a", "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST"},
    ).json()
    action = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions", headers=_headers("company-a"),
        json={"external_target_id": "r", "action_type": "send_message", "content": "hello", "idempotency_key": "readiness"},
    ).json()

    class FailingAdapter:
        def deliver(self, action, account):
            return DeliveryAdapterResult.failure("temporary_failure", "temporary")

    registry = DeliveryAdapterRegistry({"generic_hmac": FailingAdapter()})
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        OutboundIntegrationDeliveryService(session, adapter_registry=registry).deliver_pending_action(
            workspace, UUID(account["id"]), UUID(action["id"])
        )
        view = _service(session, registry).evaluate(workspace, UUID(account["id"]), UUID(action["id"]))
        assert view.ready is True
        assert view.blocking_reasons == ()

        persisted = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert persisted is not None
        persisted.status = "delivered"
        session.add(persisted)
        session.commit()
        view = _service(session, registry).evaluate(workspace, UUID(account["id"]), UUID(action["id"]))
        assert view.ready is False
        assert view.blocking_reasons == ("action_delivered",)
