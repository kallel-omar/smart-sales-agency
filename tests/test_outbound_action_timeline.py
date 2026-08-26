from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    ApprovalRequest,
    OutboundIntegrationAuditEvent,
    OutboundIntegrationDeliveryAttempt,
)
from tests.test_outbound_action_audit import _setup
from tests.test_outbound_delivery_approval_gate import _create_workspace_and_action, _headers


def _url(account: dict, action: dict) -> str:
    return (
        f"/api/integrations/accounts/{account['id']}/outbound-actions/"
        f"{action['id']}/timeline"
    )


def test_timeline_composes_safe_chronological_outbound_records(client):
    account, action = _create_workspace_and_action(client, "company-a")
    action_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(
        f"/api/approvals/{action['approval_request_id']}/approve",
        headers=_headers("company-a"),
        json={},
    ).status_code == 200
    assert client.post(f"{action_url}/deliver", headers=_headers("company-a")).status_code == 200

    response = client.get(_url(account, action), headers=_headers("company-a"))

    assert response.status_code == 200
    entries = response.json()
    assert [entry["event"] for entry in entries] == [
        "approval_requested",
        "action_created",
        "approval_approved",
        "delivery_attempt",
        "delivery_attempted",
        "action_delivered",
    ]
    assert [entry["category"] for entry in entries] == [
        "approval",
        "lifecycle",
        "approval",
        "delivery",
        "delivery",
        "lifecycle",
    ]
    assert entries == sorted(entries, key=lambda entry: entry["created_at"])
    attempt = next(entry for entry in entries if entry["event"] == "delivery_attempt")
    assert attempt["attempt_number"] == 1
    assert attempt["state"] == "delivered"
    for entry in entries:
        for field in (
            "content",
            "payload",
            "idempotency_key",
            "credential_hash",
            "secret_reference",
            "reviewer_note",
        ):
            assert field not in entry


def test_timeline_is_bounded_and_workspace_scoped(client):
    account, action = _setup(client, "company-a", "generic_webhook")
    action_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(f"{action_url}/deliver", headers=_headers("company-a")).status_code == 200
    assert client.post(f"{action_url}/retry", headers=_headers("company-a")).status_code == 200
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201

    response = client.get(
        _url(account, action), headers=_headers("company-a"), params={"limit": 2}
    )

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert client.get(_url(account, action), headers=_headers("company-b")).status_code == 404
    assert client.get(
        _url(account, action), headers=_headers("company-a"), params={"limit": 101}
    ).status_code == 422


def test_timeline_query_filters_safe_composed_entries_in_chronological_order(client):
    account, action = _create_workspace_and_action(client, "company-a")
    action_url = f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}"
    assert client.post(
        f"/api/approvals/{action['approval_request_id']}/approve",
        headers=_headers("company-a"),
        json={},
    ).status_code == 200
    assert client.post(f"{action_url}/deliver", headers=_headers("company-a")).status_code == 200

    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        approval = session.get(ApprovalRequest, UUID(action["approval_request_id"]))
        assert approval is not None
        approval.created_at = base_time
        approval.decided_at = base_time + timedelta(minutes=10)
        session.add(approval)

        audit_offsets = {"created": 20, "delivery_attempted": 40, "delivered": 50}
        audits = session.exec(
            select(OutboundIntegrationAuditEvent).where(
                OutboundIntegrationAuditEvent.outbound_integration_action_id
                == UUID(action["id"])
            )
        ).all()
        for audit in audits:
            audit.created_at = base_time + timedelta(minutes=audit_offsets[audit.action])
            session.add(audit)

        attempt = session.exec(
            select(OutboundIntegrationDeliveryAttempt).where(
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id
                == UUID(action["id"])
            )
        ).one()
        attempt.started_at = base_time + timedelta(minutes=30)
        session.add(attempt)
        session.commit()

    category_response = client.get(
        _url(account, action),
        headers=_headers("company-a"),
        params={"category": "delivery", "limit": 1},
    )
    assert category_response.status_code == 200
    assert [entry["event"] for entry in category_response.json()] == [
        "delivery_attempt"
    ]

    event_response = client.get(
        _url(account, action),
        headers=_headers("company-a"),
        params={"event": "approval_approved"},
    )
    assert event_response.status_code == 200
    assert [entry["event"] for entry in event_response.json()] == [
        "approval_approved"
    ]

    after_response = client.get(
        _url(account, action),
        headers=_headers("company-a"),
        params={"created_after": (base_time + timedelta(minutes=20)).isoformat()},
    )
    assert [entry["event"] for entry in after_response.json()] == [
        "action_created",
        "delivery_attempt",
        "delivery_attempted",
        "action_delivered",
    ]

    before_response = client.get(
        _url(account, action),
        headers=_headers("company-a"),
        params={"created_before": (base_time + timedelta(minutes=30)).isoformat()},
    )
    assert [entry["event"] for entry in before_response.json()] == [
        "approval_requested",
        "approval_approved",
        "action_created",
        "delivery_attempt",
    ]

    combined_response = client.get(
        _url(account, action),
        headers=_headers("company-a"),
        params={
            "created_after": (base_time + timedelta(minutes=10)).isoformat(),
            "created_before": (base_time + timedelta(minutes=40)).isoformat(),
        },
    )
    assert combined_response.status_code == 200
    assert [entry["event"] for entry in combined_response.json()] == [
        "approval_approved",
        "action_created",
        "delivery_attempt",
        "delivery_attempted",
    ]
    assert combined_response.json() == sorted(
        combined_response.json(), key=lambda entry: entry["created_at"]
    )
