from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import InboundIntegrationEventReceipt, OutboundIntegrationAction


def _workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _create_workspace(client, slug: str) -> None:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug})
    assert response.status_code == 201


def _workspace_id(client, slug: str) -> UUID:
    return UUID(client.get(f"/api/workspaces/{slug}").json()["id"])


def _integration_account_id(client, workspace_slug: str) -> UUID:
    response = client.get(
        "/api/integrations/accounts",
        headers=_workspace_headers(workspace_slug),
    )
    assert response.status_code == 200
    return UUID(response.json()[0]["id"])


def _create_lead(client, workspace_slug: str) -> str:
    response = client.post(
        "/api/leads",
        json={
            "tenant_id": workspace_slug,
            "full_name": "Sarra Ben Ali",
            "company_name": "Example Commerce",
            "email": "sarra@example.com",
            "source": "manual",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _inbound_payload(lead_id: str) -> dict[str, str]:
    return {
        "lead_id": lead_id,
        "channel": "website_chat",
        "content": "What is the monthly price?",
    }


def _outbound_payload(key: str, correlation_id: str | None = None) -> dict:
    payload = {
        "external_target_id": "recipient-123",
        "action_type": "send_message",
        "content": "Hello from Smart Sales Agency",
        "idempotency_key": key,
    }
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id
    return payload


def test_idempotent_inbound_event_generates_persists_and_reuses_correlation(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    workspace_id = _workspace_id(client, "company-a")
    integration_account_factory(workspace_id, "company-a-key")
    account_id = _integration_account_id(client, "company-a")
    lead_id = _create_lead(client, "company-a")
    headers, body = signed_webhook_request("company-a-key", _inbound_payload(lead_id))
    headers["X-Integration-Event-Id"] = "provider-event-123"

    first = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    duplicate = client.post("/api/integrations/inbound-events", headers=headers, content=body)

    assert first.status_code == 200
    correlation_id = UUID(first.json()["correlation_id"])
    assert duplicate.status_code == 200
    assert duplicate.json() == {"duplicate": True, "correlation_id": str(correlation_id)}

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        receipt = session.exec(
            select(InboundIntegrationEventReceipt).where(
                InboundIntegrationEventReceipt.workspace_id == workspace_id,
                InboundIntegrationEventReceipt.integration_account_id == account_id,
                InboundIntegrationEventReceipt.external_event_id == "provider-event-123",
            )
        ).one()
        assert receipt.correlation_id == correlation_id


def test_distinct_inbound_events_and_accounts_receive_independent_correlations(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    _create_workspace(client, "company-b")
    workspace_a_id = _workspace_id(client, "company-a")
    workspace_b_id = _workspace_id(client, "company-b")
    integration_account_factory(workspace_a_id, "company-a-key")
    integration_account_factory(workspace_b_id, "company-b-key")
    lead_a = _create_lead(client, "company-a")
    lead_b = _create_lead(client, "company-b")

    headers_a, body_a = signed_webhook_request("company-a-key", _inbound_payload(lead_a))
    headers_b, body_b = signed_webhook_request("company-b-key", _inbound_payload(lead_b))
    headers_a["X-Integration-Event-Id"] = "provider-event-123"
    headers_b["X-Integration-Event-Id"] = "provider-event-123"
    first_a = client.post("/api/integrations/inbound-events", headers=headers_a, content=body_a)
    first_b = client.post("/api/integrations/inbound-events", headers=headers_b, content=body_b)

    different_headers, different_body = signed_webhook_request("company-a-key", _inbound_payload(lead_a))
    different_headers["X-Integration-Event-Id"] = "provider-event-456"
    second_a = client.post(
        "/api/integrations/inbound-events", headers=different_headers, content=different_body
    )

    assert first_a.status_code == 200
    assert first_b.status_code == 200
    assert second_a.status_code == 200
    assert first_a.json()["correlation_id"] != first_b.json()["correlation_id"]
    assert first_a.json()["correlation_id"] != second_a.json()["correlation_id"]
    assert workspace_a_id != workspace_b_id


def test_outbound_actions_persist_optional_correlation_without_cross_workspace_access(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    _create_workspace(client, "company-b")
    integration_account_factory(_workspace_id(client, "company-a"), "company-a-key")
    integration_account_factory(_workspace_id(client, "company-b"), "company-b-key")
    account_b_id = _integration_account_id(client, "company-b")
    lead_a = _create_lead(client, "company-a")
    headers, body = signed_webhook_request("company-a-key", _inbound_payload(lead_a))
    headers["X-Integration-Event-Id"] = "provider-event-123"
    inbound = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    correlation_id = inbound.json()["correlation_id"]

    denied = client.post(
        f"/api/integrations/accounts/{account_b_id}/outbound-actions",
        headers=_workspace_headers("company-a"),
        json=_outbound_payload("cross-workspace", correlation_id),
    )
    assert denied.status_code == 404

    correlated = client.post(
        f"/api/integrations/accounts/{account_b_id}/outbound-actions",
        headers=_workspace_headers("company-b"),
        json=_outbound_payload("correlated", correlation_id),
    )
    uncorrelated = client.post(
        f"/api/integrations/accounts/{account_b_id}/outbound-actions",
        headers=_workspace_headers("company-b"),
        json=_outbound_payload("uncorrelated"),
    )

    assert correlated.status_code == 201
    assert correlated.json()["correlation_id"] == correlation_id
    assert uncorrelated.status_code == 201
    assert uncorrelated.json()["correlation_id"] is None
    assert client.get(
        f"/api/integrations/outbound-actions/{correlated.json()['id']}",
        headers=_workspace_headers("company-a"),
    ).status_code == 404

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        action = session.get(OutboundIntegrationAction, UUID(correlated.json()["id"]))
        assert action is not None
        assert action.correlation_id == correlation_id


def test_legacy_inbound_event_does_not_create_a_correlation_receipt(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    integration_account_factory(_workspace_id(client, "company-a"), "company-a-key")
    account_id = _integration_account_id(client, "company-a")
    lead_id = _create_lead(client, "company-a")
    headers, body = signed_webhook_request("company-a-key", _inbound_payload(lead_id))

    first = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    second = client.post("/api/integrations/inbound-events", headers=headers, content=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert "correlation_id" not in first.json()
    assert "duplicate" not in second.json()

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        receipts = session.exec(
            select(InboundIntegrationEventReceipt).where(
                InboundIntegrationEventReceipt.integration_account_id == account_id
            )
        ).all()
        assert receipts == []
