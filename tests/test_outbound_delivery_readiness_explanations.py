from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction, Workspace
from app.services.delivery_adapters import DeliveryAdapterRegistry, NoopDeliveryAdapter
from app.services.outbound_delivery_readiness import OutboundDeliveryReadinessService
from app.services.outbound_retry_delay_policy import OutboundDeliveryRetryDelayPolicy
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy
from tests.test_outbound_delivery_approval_gate import _create_workspace_and_action


def _service(session):
    return OutboundDeliveryReadinessService(
        session,
        retry_policy=OutboundDeliveryRetryPolicy(3),
        retry_delay_policy=OutboundDeliveryRetryDelayPolicy("fixed", 0, 0),
        adapter_registry=DeliveryAdapterRegistry({"approval-provider": NoopDeliveryAdapter()}),
    )


def test_readiness_explanation_exposes_only_the_relevant_safe_timestamp(client):
    account, action = _create_workspace_and_action(client)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        persisted = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert persisted is not None
        not_before = datetime.now(timezone.utc) + timedelta(minutes=5)
        persisted.requires_approval = False
        persisted.not_before = not_before
        session.add(persisted)
        session.commit()

        view = _service(session).evaluate(workspace, UUID(account["id"]), persisted.id)

        assert len(view.blocking_reason_details) == 1
        detail = view.blocking_reason_details[0]
        assert detail.code == "not_before_not_reached"
        assert detail.message == "The outbound action is not available yet."
        assert detail.not_before == not_before
        assert detail.expires_at is None
        assert detail.next_retry_at is None
