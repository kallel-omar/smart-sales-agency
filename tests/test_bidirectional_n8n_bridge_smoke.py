"""Provider-neutral smoke coverage for an n8n-compatible integration bridge."""

import hmac
import json
from hashlib import sha256
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    InboundIntegrationEventReceipt,
    OutboundIntegrationAction,
    OutboundIntegrationDeliveryAttempt,
    Workspace,
)
from app.services.delivery_adapters import (
    DeliveryAdapterRegistry,
    GenericWebhookDeliveryAdapter,
    WebhookHttpResponse,
)
from app.services.ai_invocation_gateway import AIInvocationGateway
from app.services.inbound_integrations import InboundIntegrationService
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


class RecordingN8nTransport:
    """In-process stand-in for n8n's outbound webhook receiver."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url, *, content, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "content": content,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return WebhookHttpResponse(202, {"x-delivery-id": "n8n-flow-run-1"})


class StaticSecretResolver:
    """Test-only resolver: it keeps the signing value out of persistence."""

    def resolve(self, reference: str | None) -> str | None:
        del reference
        return "outbound-smoke-signing-secret"


def _workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _provision_account(client, slug: str, provider: str) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers(slug),
        json={
            "provider": provider,
            "external_account_id": f"{slug}-{provider}",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_bidirectional_n8n_compatible_bridge_smoke_flow(
    client,
    monkeypatch,
    signed_webhook_request,
):
    """Exercise the real FastAPI/domain boundaries using only in-process fakes."""

    assert client.post(
        "/api/workspaces",
        json={"slug": "bridge-workspace", "name": "Bridge Workspace"},
    ).status_code == 201
    inbound_account = _provision_account(client, "bridge-workspace", "generic_hmac")
    outbound_account = _provision_account(client, "bridge-workspace", "generic_webhook")
    lead = client.post(
        "/api/leads",
        json={
            "tenant_id": "bridge-workspace",
            "full_name": "Sarra Ben Ali",
            "company_name": "Bridge Commerce",
            "email": "sarra@example.com",
            "source": "website",
        },
    )
    assert lead.status_code == 201

    # Demo mode keeps the Sales handoff deterministic. The inbound path now
    # owns no LLM factory; this boundary raises if it ever invokes the gateway.
    async def fail_gateway_invocation(self, request):
        del self, request
        raise AssertionError("The n8n bridge smoke flow must not invoke an LLM")

    monkeypatch.setattr(AIInvocationGateway, "invoke", fail_gateway_invocation)
    original_handle_event = InboundIntegrationService.handle_event
    handoff_count = 0

    async def count_real_sales_handoff(self, event, workspace):
        nonlocal handoff_count
        handoff_count += 1
        return await original_handle_event(self, event, workspace)

    monkeypatch.setattr(InboundIntegrationService, "handle_event", count_real_sales_handoff)

    payload = {
        "lead_id": lead.json()["id"],
        "channel": "website_chat",
        "content": "What is the monthly price?",
    }
    headers, body = signed_webhook_request(inbound_account["inbound_credential"], payload)
    headers["X-Integration-Event-Id"] = "n8n-normalized-event-1"

    first = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    assert first.status_code == 200
    correlation_id = first.json()["correlation_id"]
    assert handoff_count == 1
    history_after_first = client.get(
        f"/api/conversations/{lead.json()['id']}",
        headers=_workspace_headers("bridge-workspace"),
    )
    assert history_after_first.status_code == 200
    assert len(history_after_first.json()) == 1

    duplicate = client.post("/api/integrations/inbound-events", headers=headers, content=body)
    assert duplicate.status_code == 200
    assert duplicate.json() == {"duplicate": True, "correlation_id": correlation_id}
    assert handoff_count == 1
    history_after_duplicate = client.get(
        f"/api/conversations/{lead.json()['id']}",
        headers=_workspace_headers("bridge-workspace"),
    )
    assert len(history_after_duplicate.json()) == len(history_after_first.json())

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        receipt = session.exec(
            select(InboundIntegrationEventReceipt).where(
                InboundIntegrationEventReceipt.integration_account_id
                == UUID(inbound_account["id"]),
                InboundIntegrationEventReceipt.external_event_id == "n8n-normalized-event-1",
            )
        ).one()
        assert str(receipt.correlation_id) == correlation_id
        assert session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.correlation_id == correlation_id
            )
        ).all() == []

    # The Sales handoff deliberately does not create delivery intents. Reuse the
    # existing scoped outbound API to attach the resulting intent to this run.
    action_response = client.post(
        f"/api/integrations/accounts/{outbound_account['id']}/outbound-actions",
        headers=_workspace_headers("bridge-workspace"),
        json={
            "external_target_id": "n8n-contact-42",
            "action_type": "send_message",
            "content": "Here is the requested pricing information.",
            "payload": {"format": "plain_text"},
            "correlation_id": correlation_id,
            "idempotency_key": "n8n-normalized-event-1-reply",
            "requires_approval": True,
        },
    )
    assert action_response.status_code == 201
    action = action_response.json()
    assert action["correlation_id"] == correlation_id

    pending_readiness = client.get(
        f"/api/integrations/accounts/{outbound_account['id']}/outbound-actions/{action['id']}/delivery-readiness",
        headers=_workspace_headers("bridge-workspace"),
    )
    assert pending_readiness.status_code == 200
    assert pending_readiness.json()["ready"] is False
    assert "approval_pending" in pending_readiness.json()["blocking_reasons"]

    blocked = client.post(
        f"/api/integrations/accounts/{outbound_account['id']}/outbound-actions/{action['id']}/deliver",
        headers=_workspace_headers("bridge-workspace"),
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Outbound integration action requires approval before delivery"

    with next(session_dependency()) as session:
        assert session.exec(
            select(OutboundIntegrationDeliveryAttempt).where(
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id
                == UUID(action["id"])
            )
        ).all() == []

    approved = client.post(
        f"/api/approvals/{action['approval_request_id']}/approve",
        headers=_workspace_headers("bridge-workspace"),
        json={"reviewer_note": "approved for bridge smoke test"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    ready = client.get(
        f"/api/integrations/accounts/{outbound_account['id']}/outbound-actions/{action['id']}/delivery-readiness",
        headers=_workspace_headers("bridge-workspace"),
    )
    assert ready.status_code == 200
    assert ready.json()["ready"] is True

    transport = RecordingN8nTransport()
    adapter = GenericWebhookDeliveryAdapter(
        "https://n8n.test/webhook/outbound",
        transport=transport,
        signing_enabled=True,
        secret_resolver=StaticSecretResolver(),
        timestamp_provider=lambda: 1_700_000_000,
    )
    with next(session_dependency()) as session:
        workspace = session.exec(
            select(Workspace).where(Workspace.slug == "bridge-workspace")
        ).one()
        delivered, _ = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry({"generic_webhook": adapter}),
        ).deliver_pending_action(
            workspace,
            UUID(outbound_account["id"]),
            UUID(action["id"]),
        )
        assert delivered.status == "delivered"
        assert delivered.provider_delivery_id == "n8n-flow-run-1"

    assert len(transport.calls) == 1
    outbound_request = transport.calls[0]
    assert outbound_request["url"] == "https://n8n.test/webhook/outbound"
    assert outbound_request["headers"]["Content-Type"] == "application/json"
    assert outbound_request["headers"]["X-Webhook-Signing"] == "hmac-sha256"
    assert outbound_request["headers"]["X-Webhook-Timestamp"] == "1700000000"
    assert outbound_request["headers"]["X-Webhook-Signature"] == hmac.new(
        b"outbound-smoke-signing-secret",
        b"1700000000." + outbound_request["content"],
        sha256,
    ).hexdigest()
    assert json.loads(outbound_request["content"]) == {
        "action_id": action["id"],
        "action_type": "send_message",
        "external_target_id": "n8n-contact-42",
        "content": "Here is the requested pricing information.",
    }
    for excluded_value in (
        correlation_id.encode(),
        b"n8n-normalized-event-1-reply",
        b"outbound-smoke-signing-secret",
    ):
        assert excluded_value not in outbound_request["content"]

    trace = client.get(
        f"/api/integrations/execution-traces/{correlation_id}",
        headers=_workspace_headers("bridge-workspace"),
    )
    assert trace.status_code == 200
    assert trace.json()["inbound"]["external_event_id"] == "n8n-normalized-event-1"
    assert [row["id"] for row in trace.json()["outbound_actions"]] == [action["id"]]
    traced_action = trace.json()["outbound_actions"][0]
    assert traced_action["status"] == "delivered"
    assert traced_action["provider_delivery_id"] == "n8n-flow-run-1"
    assert traced_action["delivery_attempts"][0]["status"] == "delivered"
    assert traced_action["delivery_attempts"][0]["attempt_number"] == 1
