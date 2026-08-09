import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import OutboundIntegrationAction


def _workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _create_workspace(client, slug: str) -> None:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug})
    assert response.status_code == 201


def _workspace_id(client, slug: str) -> UUID:
    return UUID(client.get(f"/api/workspaces/{slug}").json()["id"])


def _account_id(client, slug: str) -> str:
    response = client.get("/api/integrations/accounts", headers=_workspace_headers(slug))
    assert response.status_code == 200
    return response.json()[0]["id"]


def _create_lead(client, slug: str) -> str:
    response = client.post(
        "/api/leads",
        json={
            "tenant_id": slug,
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


def _create_correlated_inbound(client, signed_webhook_request, key: str, lead_id: str, event_id: str) -> str:
    headers, body = signed_webhook_request(key, _inbound_payload(lead_id))
    headers["X-Integration-Event-Id"] = event_id
    response = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    assert response.status_code == 200
    return response.json()["correlation_id"]


def _create_action(
    client,
    slug: str,
    account_id: str,
    key: str,
    correlation_id: str | None = None,
    requires_approval: bool = True,
) -> dict:
    payload = {
        "external_target_id": f"recipient-{key}",
        "action_type": "send_message",
        "content": "Hello from Smart Sales Agency",
        "idempotency_key": key,
        "requires_approval": requires_approval,
    }
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id
    response = client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions",
        headers=_workspace_headers(slug),
        json=payload,
    )
    assert response.status_code == 201
    return response.json()


def _trace(client, slug: str, correlation_id: str):
    return client.get(
        f"/api/integrations/execution-traces/{correlation_id}",
        headers=_workspace_headers(slug),
    )


def test_trace_returns_safe_inbound_receipt_when_no_outbound_actions_exist(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    integration_account_factory(_workspace_id(client, "company-a"), "company-a-key")
    lead_id = _create_lead(client, "company-a")
    correlation_id = _create_correlated_inbound(
        client, signed_webhook_request, "company-a-key", lead_id, "event-123"
    )

    response = _trace(client, "company-a", correlation_id)

    assert response.status_code == 200
    assert response.json()["correlation_id"] == correlation_id
    assert response.json()["inbound"] == {
        "integration_account_id": _account_id(client, "company-a"),
        "provider": "generic_hmac",
        "external_account_id": "account-company-a-key",
        "external_event_id": "event-123",
        "correlation_id": correlation_id,
        "received_at": response.json()["inbound"]["received_at"],
    }
    assert response.json()["outbound_actions"] == []


def test_trace_returns_not_found_for_unknown_or_cross_workspace_correlation(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    _create_workspace(client, "company-b")
    integration_account_factory(_workspace_id(client, "company-a"), "company-a-key")
    integration_account_factory(_workspace_id(client, "company-b"), "company-b-key")
    correlation_id = _create_correlated_inbound(
        client,
        signed_webhook_request,
        "company-b-key",
        _create_lead(client, "company-b"),
        "event-123",
    )

    unknown = _trace(client, "company-a", str(uuid4()))
    cross_workspace = _trace(client, "company-a", correlation_id)

    assert unknown.status_code == cross_workspace.status_code == 404
    assert unknown.json() == cross_workspace.json() == {
        "detail": "Integration execution trace not found"
    }


def test_trace_composes_only_correlated_actions_in_deterministic_order_and_excludes_sensitive_fields(
    client,
    integration_account_factory,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    integration_account_factory(_workspace_id(client, "company-a"), "company-a-key")
    account_id = _account_id(client, "company-a")
    correlation_id = _create_correlated_inbound(
        client,
        signed_webhook_request,
        "company-a-key",
        _create_lead(client, "company-a"),
        "event-123",
    )
    first = _create_action(client, "company-a", account_id, "correlated-first", correlation_id)
    second = _create_action(client, "company-a", account_id, "correlated-second", correlation_id)
    _create_action(client, "company-a", account_id, "uncorrelated")

    session_dependency = app.dependency_overrides[get_session]
    same_created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with next(session_dependency()) as session:
        actions = session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.id.in_([UUID(first["id"]), UUID(second["id"])])
            )
        ).all()
        for action in actions:
            action.created_at = same_created_at
            session.add(action)
        session.commit()

    response = _trace(client, "company-a", correlation_id)

    assert response.status_code == 200
    actions = response.json()["outbound_actions"]
    assert [action["id"] for action in actions] == sorted([first["id"], second["id"]])
    assert [action["external_target_id"] for action in actions] == [
        first["external_target_id"] if first["id"] < second["id"] else second["external_target_id"],
        second["external_target_id"] if first["id"] < second["id"] else first["external_target_id"],
    ]
    assert all(action["requires_approval"] is True for action in actions)
    assert all(action["approval_status"] == "pending" for action in actions)
    response_text = json.dumps(response.json())
    for sensitive_field in (
        "content",
        "payload",
        "idempotency_key",
        "credential_hash",
        "secret_reference",
        "signature",
        "secret_value",
    ):
        assert sensitive_field not in response_text


def test_trace_includes_existing_delivery_attempt_history(client, integration_account_factory, signed_webhook_request):
    _create_workspace(client, "company-a")
    integration_account_factory(_workspace_id(client, "company-a"), "company-a-key")
    account_id = _account_id(client, "company-a")
    correlation_id = _create_correlated_inbound(
        client,
        signed_webhook_request,
        "company-a-key",
        _create_lead(client, "company-a"),
        "event-123",
    )
    action = _create_action(
        client,
        "company-a",
        account_id,
        "deliver-me",
        correlation_id,
        requires_approval=False,
    )

    delivery = client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions/{action['id']}/deliver",
        headers=_workspace_headers("company-a"),
    )
    response = _trace(client, "company-a", correlation_id)

    assert delivery.status_code == 200
    assert response.status_code == 200
    traced_action = response.json()["outbound_actions"][0]
    assert traced_action["id"] == action["id"]
    assert traced_action["status"] == "delivered"
    assert traced_action["delivery_attempts"] == [
        {
            "id": traced_action["delivery_attempts"][0]["id"],
            "attempt_number": 1,
            "status": "delivered",
            "provider_delivery_id": f"noop-{action['id']}",
            "started_at": traced_action["delivery_attempts"][0]["started_at"],
            "completed_at": traced_action["delivery_attempts"][0]["completed_at"],
            "failure_code": None,
            "failure_message": None,
        }
    ]
