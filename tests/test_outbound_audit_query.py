from datetime import timedelta
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAuditEvent, utc_now


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _setup(client, slug: str, provider: str = "generic_hmac") -> tuple[dict, dict]:
    assert client.post("/api/workspaces", json={"slug": slug, "name": slug}).status_code == 201
    account = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={
            "provider": provider,
            "external_account_id": slug,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    action = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_headers(slug),
        json={
            "external_target_id": "recipient",
            "action_type": "send_message",
            "content": "private content",
            "payload": {"private": True},
            "idempotency_key": slug,
        },
    ).json()
    return account, action


def test_outbound_audit_query_filters_orders_and_omits_sensitive_fields(client):
    account, first = _setup(client, "company-a")
    second = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_headers("company-a"),
        json={
            "external_target_id": "recipient-2",
            "action_type": "send_message",
            "content": "also private",
            "idempotency_key": "second",
        },
    ).json()
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        first_event = session.exec(
                select(OutboundIntegrationAuditEvent).where(
                    OutboundIntegrationAuditEvent.outbound_integration_action_id == UUID(first["id"])
                )
        ).one()
        first_event.created_at = utc_now() - timedelta(days=1)
        session.add(first_event)
        session.commit()

    response = client.get(
        "/api/integrations/outbound-audit-events",
        headers=_headers("company-a"),
        params={
            "action": "created",
            "integration_account_id": account["id"],
            "limit": 1,
        },
    )

    assert response.status_code == 200
    assert [event["outbound_integration_action_id"] for event in response.json()] == [second["id"]]
    event = response.json()[0]
    for sensitive in (
        "content",
        "payload",
        "correlation_id",
        "idempotency_key",
        "credential_hash",
        "secret_reference",
    ):
        assert sensitive not in event


def test_outbound_audit_query_is_workspace_scoped_and_validates_filters(client):
    _, company_a_action = _setup(client, "company-a")
    _, company_b_action = _setup(client, "company-b")

    response = client.get(
        "/api/integrations/outbound-audit-events",
        headers=_headers("company-a"),
        params={"outbound_integration_action_id": company_b_action["id"]},
    )
    assert response.status_code == 200
    assert response.json() == []

    response = client.get(
        "/api/integrations/outbound-audit-events",
        headers=_headers("company-a"),
        params={"outbound_integration_action_id": company_a_action["id"]},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    invalid_range = client.get(
        "/api/integrations/outbound-audit-events",
        headers=_headers("company-a"),
        params={
            "created_after": "2026-02-01T00:00:00Z",
            "created_before": "2026-01-01T00:00:00Z",
        },
    )
    assert invalid_range.status_code == 422
    assert client.get(
        "/api/integrations/outbound-audit-events",
        headers=_headers("company-a"),
        params={"limit": 101},
    ).status_code == 422
