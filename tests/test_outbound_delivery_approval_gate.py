from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import ApprovalRequest, OutboundIntegrationDeliveryAttempt, Workspace
from app.services.delivery_adapters import DeliveryAdapterRegistry, DeliveryAdapterResult
from app.services.outbound_delivery import OutboundIntegrationDeliveryService


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _create_workspace_and_action(client, slug: str = "company-a") -> tuple[dict, dict]:
    assert client.post("/api/workspaces", json={"slug": slug, "name": slug}).status_code == 201
    account = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={
            "provider": "approval-provider",
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
            "content": "Sensitive delivery content",
            "payload": {"private": "value"},
            "idempotency_key": f"approval-{slug}",
            "requires_approval": True,
        },
    )
    assert action.status_code == 201
    return account, action.json()


def test_pending_outbound_approval_blocks_delivery_without_creating_an_attempt(client):
    account, action = _create_workspace_and_action(client)
    response = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver",
        headers=_headers("company-a"),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Outbound integration action requires approval before delivery"
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        attempts = session.exec(select(OutboundIntegrationDeliveryAttempt)).all()
        approval = session.get(ApprovalRequest, UUID(action["approval_request_id"]))
        assert attempts == []
        assert approval is not None
        assert approval.payload == {}


def test_pending_whatsapp_cloud_approval_blocks_delivery_before_transport(client):
    slug = "whatsapp-approval"
    assert client.post("/api/workspaces", json={"slug": slug, "name": slug}).status_code == 201
    account = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={
            "provider": "whatsapp_cloud",
            "external_account_id": "555666777888999",
            "secret_reference": "INTEGRATION_SECRET_WHATSAPP_CLOUD",
        },
    ).json()
    created = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_headers(slug),
        json={
            "external_target_id": "15557654321",
            "action_type": "send_message",
            "content": "Approved WhatsApp text only",
            "payload": {},
            "idempotency_key": "whatsapp-approval-gate",
            "requires_approval": True,
        },
    )
    assert created.status_code == 201

    response = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{created.json()['id']}/deliver",
        headers=_headers(slug),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Outbound integration action requires approval before delivery"
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.exec(select(OutboundIntegrationDeliveryAttempt)).all() == []


def test_approved_outbound_action_can_deliver_and_rejected_action_stays_blocked(client):
    account, action = _create_workspace_and_action(client)
    approval_id = action["approval_request_id"]
    approved = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=_headers("company-a"),
        json={"reviewer_note": "approved"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["payload"] == {}

    class RecordingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def deliver(self, action, account):
            self.calls += 1
            return DeliveryAdapterResult.success("provider-1")

    adapter = RecordingAdapter()
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "company-a")).one()
        delivered, _ = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry({"approval-provider": adapter}),
        ).deliver_pending_action(workspace, UUID(account["id"]), UUID(action["id"]))
        assert delivered.status == "delivered"
        assert adapter.calls == 1

    rejected_account, rejected_action = _create_workspace_and_action(client, "company-b")
    rejected = client.post(
        f"/api/approvals/{rejected_action['approval_request_id']}/reject",
        headers=_headers("company-b"),
        json={"reviewer_note": "rejected"},
    )
    assert rejected.status_code == 200
    blocked = client.post(
        f"/api/integrations/accounts/{rejected_account['id']}/outbound-actions/{rejected_action['id']}/deliver",
        headers=_headers("company-b"),
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Outbound integration action approval was rejected"


def test_outbound_approval_is_workspace_scoped(client):
    _, action = _create_workspace_and_action(client, "company-a")
    assert client.post("/api/workspaces", json={"slug": "company-b", "name": "company-b"}).status_code == 201
    response = client.post(
        f"/api/approvals/{action['approval_request_id']}/approve",
        headers=_headers("company-b"),
        json={},
    )
    assert response.status_code == 404
