from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction, Workspace
from app.services.delivery_adapters import DeliveryAdapterRegistry, NoopDeliveryAdapter
from app.services.outbound_delivery_readiness import OutboundDeliveryReadinessService
from app.services.outbound_delivery_readiness_reasons import (
    OutboundDeliveryReadinessReasonCode,
    readiness_reason_message,
)
from app.services.outbound_retry_delay_policy import OutboundDeliveryRetryDelayPolicy
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy
from tests.test_outbound_delivery_approval_gate import _create_workspace_and_action


def _service(session):
    return OutboundDeliveryReadinessService(
        session,
        retry_policy=OutboundDeliveryRetryPolicy(3),
        retry_delay_policy=OutboundDeliveryRetryDelayPolicy("fixed", 0, 0),
        adapter_registry=DeliveryAdapterRegistry({"generic_hmac": NoopDeliveryAdapter()}),
    )


def test_readiness_reason_registry_uses_stable_codes_and_separate_safe_messages(client):
    account, action = _create_workspace_and_action(client)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        view = _service(session).evaluate(workspace, UUID(account["id"]), UUID(action["id"]))

        assert view.blocking_reasons == (OutboundDeliveryReadinessReasonCode.APPROVAL_PENDING,)
        assert readiness_reason_message(view.blocking_reasons[0]) == (
            "The required delivery approval is still pending."
        )


def test_readiness_registry_normalizes_timing_capability_and_terminal_reasons(client):
    account, action = _create_workspace_and_action(client)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        persisted = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert persisted is not None
        persisted.requires_approval = False
        persisted.not_before = datetime.now(UTC) + timedelta(minutes=1)
        session.add(persisted)
        session.commit()
        assert _service(session).evaluate(
            workspace, UUID(account["id"]), persisted.id
        ).blocking_reasons == (OutboundDeliveryReadinessReasonCode.NOT_BEFORE_NOT_REACHED,)

        persisted.not_before = None
        persisted.action_type = "send_media"
        session.add(persisted)
        session.commit()
        assert _service(session).evaluate(
            workspace, UUID(account["id"]), persisted.id
        ).blocking_reasons == (OutboundDeliveryReadinessReasonCode.ADAPTER_CAPABILITY_MISMATCH,)

        persisted.status = "cancelled"
        session.add(persisted)
        session.commit()
        assert _service(session).evaluate(
            workspace, UUID(account["id"]), persisted.id
        ).blocking_reasons == (OutboundDeliveryReadinessReasonCode.ACTION_CANCELLED,)
