from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction, OutboundIntegrationDeliveryAttempt
from tests.test_outbound_delivery_approval_gate import _headers


def _setup(client, *, not_before: str):
    assert client.post("/api/workspaces", json={"slug": "company-a", "name": "company-a"}).status_code == 201
    account = client.post(
        "/api/integrations/accounts",
        headers=_headers("company-a"),
        json={"provider": "generic_hmac", "external_account_id": "account", "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST"},
    ).json()
    action = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_headers("company-a"),
        json={"external_target_id": "recipient", "action_type": "send_message", "content": "hello", "idempotency_key": "not-before", "not_before": not_before},
    )
    return account, action


def test_future_not_before_blocks_delivery_without_mutation_or_attempt(client):
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    account, created = _setup(client, not_before=future.isoformat())
    assert created.status_code == 201
    action = created.json()
    assert action["not_before"].endswith("Z")

    response = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver",
        headers=_headers("company-a"),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Outbound integration action is not available before its not-before time"
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        persisted = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert persisted is not None
        assert persisted.status == "pending"
        assert session.exec(select(OutboundIntegrationDeliveryAttempt)).all() == []


def test_not_before_is_utc_normalized_and_rejects_naive_times(client):
    offset_time = datetime.now(timezone(timedelta(hours=2))) + timedelta(minutes=5)
    _, accepted = _setup(client, not_before=offset_time.isoformat())
    assert accepted.status_code == 201
    assert accepted.json()["not_before"].endswith("Z")

    account_id = accepted.request.url.path.split("/")[4]
    rejected = client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions",
        headers=_headers("company-a"),
        json={"external_target_id": "recipient", "action_type": "send_message", "content": "hello", "idempotency_key": "not-before-naive", "not_before": "2030-01-01T12:00:00"},
    )
    assert rejected.status_code == 422
