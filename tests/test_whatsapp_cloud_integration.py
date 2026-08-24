
import hmac
import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlmodel import select

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.capabilities import BusinessCapabilityKey
from app.api.dependencies import get_settings
from app.db import get_session
from app.main import app
from app.models import (
    AIEmployeeCapabilityAssignment,
    AIInvocationUsage,
    ApprovalRequest,
    ApprovalStatus,
    Capability,
    Contact,
    ConversationMessage,
    Department,
    InboundExternalIdentity,
    InboundIntegrationEventReceipt,
    IntegrationAccount,
    Lead,
    OutboundIntegrationActionStatus,
    OutboundIntegrationAuditEvent,
    OutboundIntegrationDeliveryAttempt,
    OutboundIntegrationAction,
    OutboundIntegrationActionType,
    SalesConversationHandoff,
    User,
    WorkItem,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.services.ai_employee_capability_assignments import (
    AIEmployeeCapabilityAssignmentService,
)
from app.services.ai_employee_tool_access import AIEmployeeCapabilityToolAccessService
from app.services.ai_employees import AIEmployeeService
from app.services.capabilities import CapabilityService
from app.services.departments import DepartmentService
from app.services.delivery_adapters import (
    HttpxWhatsAppCloudHttpTransport,
    WhatsAppCloudDeliveryAdapter,
    WhatsAppCloudHttpResponse,
)
from app.services.lead_capture import LeadCaptureService
from app.services.outbound_delivery import OutboundIntegrationDeliveryService

PHONE_NUMBER_ID = "555666777888999"
WRONG_PHONE_NUMBER_ID = "999888777666555"
CUSTOMER_EXTERNAL_ID = "15557654321"
EVENT_ID = "wamid.HBgLMTU1NTc2NTQzMjEVAgASGBQzQUMzRjA0N0Y2MzY2QzA0AA=="
ENDPOINT = "/api/integrations/inbound-events/whatsapp-cloud"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "whatsapp_cloud"


def _workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _create_workspace(client, slug: str) -> None:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug})
    assert response.status_code == 201
    _provision_sales_workforce(slug)


def _provision_sales_workforce(slug: str) -> None:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        department = DepartmentService(session).ensure_sales_department(workspace)
        capabilities = {
            capability.key: capability
            for capability in CapabilityService(session).ensure_sales_capabilities(
                workspace, department
            )
        }
        employee = AIEmployeeService(session).create_for_department(
            workspace,
            department,
            AIEmployeeRoleKey.SALES_CONVERSATION,
            name="WhatsApp Sales",
        )
        assignments = AIEmployeeCapabilityAssignmentService(session)
        assignments.assign(
            workspace,
            employee,
            capabilities[BusinessCapabilityKey.ANSWER_CUSTOMER],
        )
        assignments.assign(
            workspace,
            employee,
            capabilities[BusinessCapabilityKey.SEND_MESSAGE],
        )


def _create_legacy_workspace(client, slug: str, *, provision_workforce: bool = True) -> None:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        user = session.exec(select(User)).first()
        assert user is not None
        workspace = Workspace(slug=slug, name=slug)
        session.add(workspace)
        session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceMemberRole.OWNER,
            )
        )
        session.commit()
    if provision_workforce:
        _provision_sales_workforce(slug)


def _create_lead(client, slug: str, *, phone: str = CUSTOMER_EXTERNAL_ID) -> str:
    response = client.post(
        "/api/leads",
        headers=_workspace_headers(slug),
        json={
            "tenant_id": slug,
            "full_name": "Example WhatsApp Lead",
            "company_name": "Example Commerce",
            "phone": phone,
            "source": "whatsapp_cloud",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _provision_account(
    client,
    slug: str,
    *,
    provider: str = "whatsapp_cloud",
    phone_number_id: str = PHONE_NUMBER_ID,
    grant_tool_access: bool = True,
    autonomy_level: AIEmployeeAutonomyLevel = (
        AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION
    ),
) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers(slug),
        json={
            "provider": provider,
            "external_account_id": phone_number_id,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    account_data = response.json()
    if not grant_tool_access:
        return account_data
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        account = session.get(IntegrationAccount, UUID(account_data["id"]))
        assert account is not None
        capability = session.exec(
            select(Capability).where(
                Capability.workspace_id == workspace.id,
                Capability.key == BusinessCapabilityKey.SEND_MESSAGE,
            )
        ).one()
        assignment = session.exec(
            select(AIEmployeeCapabilityAssignment).where(
                AIEmployeeCapabilityAssignment.workspace_id == workspace.id,
                AIEmployeeCapabilityAssignment.capability_id == capability.id,
            )
        ).one()
        AIEmployeeCapabilityToolAccessService(session).grant(
            workspace,
            assignment,
            account,
            OutboundIntegrationActionType.SEND_MESSAGE,
            autonomy_level,
        )
    return account_data


def _normalized_payload(**overrides) -> dict:
    payload = {
        "channel": "whatsapp_cloud",
        "provider_event_id": EVENT_ID,
        "sender_external_id": CUSTOMER_EXTERNAL_ID,
        "recipient_account_id": PHONE_NUMBER_ID,
        "content": "What is the monthly price?",
        "timestamp": 1_720_000_000,
        "provider_metadata": {
            "waba_id": "111122223333444",
            "display_phone_number": "15551234567",
        },
    }
    payload.update(overrides)
    return payload


def _signed_request(signed_webhook_request, account: dict, payload: dict):
    return signed_webhook_request(
        account["inbound_credential"],
        payload,
        event_id=payload["provider_event_id"],
    )

def _raw_fixture(name: str) -> dict:
    return json.loads(
        (FIXTURE_DIR / name).read_text(encoding="utf-8")
    )


def _configure_credential_reference(
    client,
    slug: str,
    account: dict,
    purpose: str,
    secret_reference: str,
) -> None:
    response = client.put(
        (
            f"/api/integrations/accounts/{account['id']}"
            f"/credential-references/{purpose}"
        ),
        headers=_workspace_headers(slug),
        json={"secret_reference": secret_reference},
    )
    assert response.status_code == 200


def _signed_meta_request(
    fixture_name: str,
    secret: str,
) -> tuple[dict[str, str], bytes]:
    payload = _raw_fixture(fixture_name)
    body = json.dumps(
        payload,
        separators=(",", ":"),
    ).encode()

    signature = "sha256=" + hmac.new(
        secret.encode(),
        body,
        sha256,
    ).hexdigest()

    return (
        {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
        },
        body,
    )


def _message_count(lead_id: str) -> int:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        return len(
            session.exec(
                select(ConversationMessage).where(ConversationMessage.lead_id == UUID(lead_id))
            ).all()
        )


def test_whatsapp_cloud_text_event_uses_machine_auth_and_creates_one_turn(
    client,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    lead_id = _create_lead(client, "company-a")
    account = _provision_account(client, "company-a")
    headers, body = _signed_request(signed_webhook_request, account, _normalized_payload())

    response = client.post(ENDPOINT, headers=headers, content=body)

    assert response.status_code == 200
    data = response.json()
    assert data["lead_id"] == lead_id
    assert data["correlation_id"]
    history = client.get(
        f"/api/conversations/{lead_id}",
        headers=_workspace_headers("company-a"),
    )
    assert history.status_code == 200
    assert history.json()[0]["direction"] == "inbound"
    assert history.json()[0]["channel"] == "whatsapp_cloud"
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        lead = session.get(Lead, UUID(lead_id))
        capture_items = list(
            session.exec(select(WorkItem).where(WorkItem.work_type == "lead_capture")).all()
        )
        assert lead and lead.contact_id is not None
        contact = session.get(Contact, lead.contact_id)
        assert contact and contact.phone == CUSTOMER_EXTERNAL_ID
        assert len(capture_items) == 2
        assert capture_items[-1].input["message"] == "What is the monthly price?"
        assert capture_items[-1].input["external_reference"] == EVENT_ID
        assert capture_items[-1].input["metadata"] == {"timestamp": 1_720_000_000}


def test_duplicate_whatsapp_event_reuses_task251_receipt_without_second_turn(
    client,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    lead_id = _create_lead(client, "company-a")
    account = _provision_account(client, "company-a")
    headers, body = _signed_request(signed_webhook_request, account, _normalized_payload())

    first = client.post(ENDPOINT, headers=headers, content=body)
    duplicate = client.post(ENDPOINT, headers=headers, content=body)

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "duplicate": True,
        "correlation_id": first.json()["correlation_id"],
    }
    # One governed sales turn persists the inbound message and its assistant reply;
    # replay must not add either message again.
    assert _message_count(lead_id) == 2
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        receipt = session.exec(select(InboundIntegrationEventReceipt)).one()
        assert receipt.external_event_id == EVENT_ID
        assert len(session.exec(select(Lead)).all()) == 1
        assert (
            len(session.exec(select(WorkItem).where(WorkItem.work_type == "lead_capture")).all())
            == 2
        )


def test_whatsapp_first_delivery_can_capture_a_new_lead(
    client,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    account = _provision_account(client, "company-a")
    headers, body = _signed_request(signed_webhook_request, account, _normalized_payload())

    response = client.post(ENDPOINT, headers=headers, content=body)

    assert response.status_code == 200
    lead_id = UUID(response.json()["lead_id"])
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        lead = session.get(Lead, lead_id)
        assert lead and lead.tenant_id == "company-a"
        assert lead.phone == CUSTOMER_EXTERNAL_ID
        assert lead.contact_id is not None
        assert (
            len(
                session.exec(
                    select(ConversationMessage).where(ConversationMessage.lead_id == lead_id)
                ).all()
            )
            == 2
        )


def test_legacy_workspace_whatsapp_capture_ensures_foundation(
    client,
    signed_webhook_request,
):
    _create_legacy_workspace(client, "legacy-whatsapp")
    account = _provision_account(client, "legacy-whatsapp")
    headers, body = _signed_request(signed_webhook_request, account, _normalized_payload())

    response = client.post(ENDPOINT, headers=headers, content=body)

    assert response.status_code == 200
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == "legacy-whatsapp")).one()
        assert (
            len(
                session.exec(
                    select(Department).where(Department.workspace_id == workspace.id)
                ).all()
            )
            == 1
        )
        assert (
            len(
                session.exec(
                    select(Capability).where(Capability.workspace_id == workspace.id)
                ).all()
            )
            == 6
        )


def test_invalid_whatsapp_auth_does_not_provision_legacy_workspace(
    client,
    signed_webhook_request,
):
    _create_legacy_workspace(
        client,
        "legacy-invalid-auth",
        provision_workforce=False,
    )
    account = _provision_account(
        client,
        "legacy-invalid-auth",
        grant_tool_access=False,
    )
    headers, body = _signed_request(signed_webhook_request, account, _normalized_payload())
    headers["X-Webhook-Signature"] = "invalid"

    response = client.post(ENDPOINT, headers=headers, content=body)

    assert response.status_code == 401
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(
            select(Workspace).where(Workspace.slug == "legacy-invalid-auth")
        ).one()
        assert (
            session.exec(select(Department).where(Department.workspace_id == workspace.id)).all()
            == []
        )
        assert (
            session.exec(select(Capability).where(Capability.workspace_id == workspace.id)).all()
            == []
        )


def test_capture_failure_releases_receipt_for_one_successful_retry(
    client,
    signed_webhook_request,
    monkeypatch,
):
    _create_workspace(client, "capture-retry")
    account = _provision_account(client, "capture-retry")
    headers, body = _signed_request(signed_webhook_request, account, _normalized_payload())
    original_capture = LeadCaptureService.capture
    attempts = 0

    def fail_once(self, workspace_id, signal):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("forced capture failure")
        return original_capture(self, workspace_id, signal)

    monkeypatch.setattr(LeadCaptureService, "capture", fail_once)

    with pytest.raises(RuntimeError, match="forced capture failure"):
        client.post(ENDPOINT, headers=headers, content=body)
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.exec(select(InboundIntegrationEventReceipt)).all() == []
        assert session.exec(select(ConversationMessage)).all() == []

    retry = client.post(ENDPOINT, headers=headers, content=body)
    duplicate = client.post(ENDPOINT, headers=headers, content=body)

    assert retry.status_code == duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    with next(session_dependency()) as session:
        assert len(session.exec(select(InboundIntegrationEventReceipt)).all()) == 1
        assert len(session.exec(select(ConversationMessage)).all()) == 2
        assert len(session.exec(select(Lead)).all()) == 1
        assert (
            len(session.exec(select(WorkItem).where(WorkItem.work_type == "lead_capture")).all())
            == 1
        )


def test_whatsapp_explicit_human_request_creates_handoff_without_ai(
    client,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    lead_id = _create_lead(client, "company-a")
    account = _provision_account(client, "company-a")
    payload = _normalized_payload(content="I need a human agent now - task287-live-5")
    headers, body = _signed_request(signed_webhook_request, account, payload)

    response = client.post(ENDPOINT, headers=headers, content=body)

    assert response.status_code == 200
    data = response.json()
    assert data["lead_id"] == lead_id
    assert data["handoff_required"] is True
    assert data["handoff_reason_code"] == "human_requested"
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        handoffs = list(session.exec(select(SalesConversationHandoff)).all())
        usage = list(session.exec(select(AIInvocationUsage)).all())
        messages = list(session.exec(select(ConversationMessage)).all())
    assert len(handoffs) == 1
    assert handoffs[0].reason_code == "human_requested"
    assert usage == []
    assert [message.content for message in messages] == [
        "I need a human agent now - task287-live-5",
        "I can't confirm that request right now. A team member needs to assist with this request.",
    ]


def test_workspace_fields_are_rejected_or_ignored_without_tenant_authority(
    client,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    _create_lead(client, "company-a")
    account = _provision_account(client, "company-a")
    payload = _normalized_payload(workspace_slug="company-b")
    headers, body = _signed_request(signed_webhook_request, account, payload)

    rejected = client.post(ENDPOINT, headers=headers, content=body)
    assert rejected.status_code == 422

    safe_payload = _normalized_payload(
        provider_metadata={"workspace_slug": "company-b", "workspace_id": "forged"}
    )
    safe_headers, safe_body = _signed_request(
        signed_webhook_request,
        account,
        safe_payload,
    )
    accepted = client.post(ENDPOINT, headers=safe_headers, content=safe_body)
    assert accepted.status_code == 200


def test_wrong_phone_reference_cannot_resolve_another_workspace(
    client,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    _create_workspace(client, "company-b")
    _create_lead(client, "company-a")
    lead_b = _create_lead(client, "company-b")
    account_a = _provision_account(client, "company-a", phone_number_id=PHONE_NUMBER_ID)
    _provision_account(client, "company-b", phone_number_id=WRONG_PHONE_NUMBER_ID)
    payload = _normalized_payload(recipient_account_id=WRONG_PHONE_NUMBER_ID)
    headers, body = _signed_request(signed_webhook_request, account_a, payload)

    response = client.post(ENDPOINT, headers=headers, content=body)

    assert response.status_code == 404
    assert response.json()["detail"] == "Integration account not found"
    assert _message_count(lead_b) == 0


def test_human_bearer_authentication_is_not_provider_machine_auth(client):
    _create_workspace(client, "company-a")
    _create_lead(client, "company-a")

    response = client.post(ENDPOINT, json=_normalized_payload())

    assert response.status_code == 422


def test_generic_provider_account_cannot_use_whatsapp_endpoint(
    client,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    _create_lead(client, "company-a")
    account = _provision_account(client, "company-a", provider="generic_hmac")
    headers, body = _signed_request(signed_webhook_request, account, _normalized_payload())

    response = client.post(ENDPOINT, headers=headers, content=body)

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook authentication"


def test_customer_sender_external_id_does_not_create_platform_user_identity(
    client,
    signed_webhook_request,
):
    _create_workspace(client, "company-a")
    _create_lead(client, "company-a")
    account = _provision_account(client, "company-a")
    headers, body = _signed_request(signed_webhook_request, account, _normalized_payload())
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        initial_user_count = len(session.exec(select(User)).all())

    response = client.post(ENDPOINT, headers=headers, content=body)

    assert response.status_code == 200
    with next(session_dependency()) as session:
        assert len(session.exec(select(User)).all()) == initial_user_count


def test_whatsapp_outbound_action_rejects_provider_token_persistence(client):
    _create_workspace(client, "company-a")
    account = _provision_account(client, "company-a")
    response = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_workspace_headers("company-a"),
        json={
            "external_target_id": CUSTOMER_EXTERNAL_ID,
            "action_type": "send_message",
            "content": "Here is the pricing information.",
            "payload": {"access_token": "fake-token"},
            "idempotency_key": "whatsapp-reply-1",
        },
    )

    assert response.status_code == 422
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.exec(select(OutboundIntegrationAction)).all() == []


def test_direct_whatsapp_webhook_verification_uses_account_verify_token(
    client,
    monkeypatch,
):
    slug = "direct-wa-verify"
    _create_workspace(client, slug)
    account = _provision_account(client, slug)

    monkeypatch.setenv(
        "INTEGRATION_SECRET_WHATSAPP_VERIFY_TOKEN",
        "hiri-meta-verify-token",
    )

    _configure_credential_reference(
        client,
        slug,
        account,
        "webhook_verify_token",
        "INTEGRATION_SECRET_WHATSAPP_VERIFY_TOKEN",
    )

    endpoint = f"{ENDPOINT}/{account['id']}"

    response = client.get(
        endpoint,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "hiri-meta-verify-token",
            "hub.challenge": "123456789",
        },
    )

    assert response.status_code == 200
    assert response.text == "123456789"

    rejected = client.get(
        endpoint,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "123456789",
        },
    )

    assert rejected.status_code == 401


def test_direct_whatsapp_text_enters_existing_sales_pipeline(
    client,
    monkeypatch,
):
    slug = "direct-wa-text"
    _create_workspace(client, slug)
    lead_id = _create_lead(client, slug)
    account = _provision_account(client, slug)

    monkeypatch.setenv(
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
        "test-whatsapp-app-secret",
    )

    _configure_credential_reference(
        client,
        slug,
        account,
        "webhook_app_secret",
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
    )

    headers, body = _signed_meta_request(
        "valid_text.json",
        "test-whatsapp-app-secret",
    )

    response = client.post(
        f"{ENDPOINT}/{account['id']}",
        headers=headers,
        content=body,
    )

    assert response.status_code == 200

    data = response.json()
    assert data["lead_id"] == lead_id
    assert data["correlation_id"]


def test_direct_whatsapp_duplicate_event_is_idempotent(
    client,
    monkeypatch,
):
    slug = "direct-wa-duplicate"
    _create_workspace(client, slug)
    lead_id = _create_lead(client, slug)
    account = _provision_account(client, slug)

    monkeypatch.setenv(
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
        "test-whatsapp-app-secret",
    )

    _configure_credential_reference(
        client,
        slug,
        account,
        "webhook_app_secret",
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
    )

    headers, body = _signed_meta_request(
        "valid_text.json",
        "test-whatsapp-app-secret",
    )

    endpoint = f"{ENDPOINT}/{account['id']}"

    first = client.post(
        endpoint,
        headers=headers,
        content=body,
    )

    assert first.status_code == 200

    messages_after_first = _message_count(lead_id)

    duplicate = client.post(
        endpoint,
        headers=headers,
        content=body,
    )

    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert _message_count(lead_id) == messages_after_first


def test_direct_whatsapp_rejects_invalid_meta_signature(
    client,
    monkeypatch,
):
    slug = "direct-wa-invalid-signature"
    _create_workspace(client, slug)
    account = _provision_account(client, slug)

    monkeypatch.setenv(
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
        "test-whatsapp-app-secret",
    )

    _configure_credential_reference(
        client,
        slug,
        account,
        "webhook_app_secret",
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
    )

    _, body = _signed_meta_request(
        "valid_text.json",
        "test-whatsapp-app-secret",
    )

    response = client.post(
        f"{ENDPOINT}/{account['id']}",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=invalid",
        },
        content=body,
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook authentication"


def test_direct_whatsapp_requires_webhook_app_secret_reference(
    client,
):
    slug = "direct-wa-missing-secret"
    _create_workspace(client, slug)
    account = _provision_account(client, slug)

    headers, body = _signed_meta_request(
        "valid_text.json",
        "some-secret",
    )

    response = client.post(
        f"{ENDPOINT}/{account['id']}",
        headers=headers,
        content=body,
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook authentication"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "status_only.json",
        "unsupported_media.json",
    ],
)
def test_direct_whatsapp_acknowledges_valid_non_text_events(
    client,
    monkeypatch,
    fixture_name,
):
    slug = f"direct-wa-ignore-{fixture_name.replace('.', '-')}"
    _create_workspace(client, slug)
    account = _provision_account(client, slug)

    monkeypatch.setenv(
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
        "test-whatsapp-app-secret",
    )

    _configure_credential_reference(
        client,
        slug,
        account,
        "webhook_app_secret",
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
    )

    headers, body = _signed_meta_request(
        fixture_name,
        "test-whatsapp-app-secret",
    )

    response = client.post(
        f"{ENDPOINT}/{account['id']}",
        headers=headers,
        content=body,
    )

    assert response.status_code == 204


def test_direct_whatsapp_rejects_provider_account_mismatch(
    client,
    monkeypatch,
):
    slug = "direct-wa-account-mismatch"
    _create_workspace(client, slug)
    account = _provision_account(client, slug)

    monkeypatch.setenv(
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
        "test-whatsapp-app-secret",
    )

    _configure_credential_reference(
        client,
        slug,
        account,
        "webhook_app_secret",
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
    )

    headers, body = _signed_meta_request(
        "wrong_account_phone.json",
        "test-whatsapp-app-secret",
    )

    response = client.post(
        f"{ENDPOINT}/{account['id']}",
        headers=headers,
        content=body,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Integration account not found"


def test_direct_whatsapp_rejects_malformed_signed_payload(
    client,
    monkeypatch,
):
    slug = "direct-wa-malformed"
    _create_workspace(client, slug)
    account = _provision_account(client, slug)

    monkeypatch.setenv(
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
        "test-whatsapp-app-secret",
    )

    _configure_credential_reference(
        client,
        slug,
        account,
        "webhook_app_secret",
        "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
    )

    headers, body = _signed_meta_request(
        "malformed.json",
        "test-whatsapp-app-secret",
    )

    response = client.post(
        f"{ENDPOINT}/{account['id']}",
        headers=headers,
        content=body,
    )

    assert response.status_code == 422


def test_direct_whatsapp_raw_e2e_uses_native_delivery_without_secret_leakage(
    client,
    monkeypatch,
    caplog,
):
    workspace_slug = "direct-wa-e2e"
    isolated_slug = "direct-wa-e2e-isolated"
    app_secret_reference = "INTEGRATION_SECRET_DIRECT_WA_E2E_APP_SECRET"
    access_token_reference = "INTEGRATION_SECRET_DIRECT_WA_E2E_ACCESS_TOKEN"
    app_secret = "direct-wa-e2e-app-secret-value"
    access_token = "direct-wa-e2e-access-token-value"
    provider_delivery_id = "wamid.direct-wa-e2e-delivery"
    direct_event_id = _raw_fixture("valid_text.json")["entry"][0]["changes"][0][
        "value"
    ]["messages"][0]["id"]
    transport_calls: list[dict] = []

    _create_workspace(client, workspace_slug)
    _create_workspace(client, isolated_slug)
    account = _provision_account(client, workspace_slug)
    monkeypatch.setenv(app_secret_reference, app_secret)
    monkeypatch.setenv(access_token_reference, access_token)
    _configure_credential_reference(
        client,
        workspace_slug,
        account,
        "webhook_app_secret",
        app_secret_reference,
    )
    _configure_credential_reference(
        client,
        workspace_slug,
        account,
        "api_access_token",
        access_token_reference,
    )

    def fake_meta_post(self, url, *, payload, headers, timeout):
        del self
        transport_calls.append(
            {
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return WhatsAppCloudHttpResponse(
            status_code=200,
            headers={},
            body={"messages": [{"id": provider_delivery_id}]},
        )

    monkeypatch.setattr(
        HttpxWhatsAppCloudHttpTransport,
        "post",
        fake_meta_post,
    )
    headers, body = _signed_meta_request("valid_text.json", app_secret)
    endpoint = f"{ENDPOINT}/{account['id']}"

    first = client.post(endpoint, headers=headers, content=body)
    duplicate = client.post(endpoint, headers=headers, content=body)

    assert first.status_code == duplicate.status_code == 200
    assert duplicate.json() == {
        "duplicate": True,
        "correlation_id": first.json()["correlation_id"],
    }
    assert len(transport_calls) == 1
    transport_call = transport_calls[0]
    assert transport_call["url"].endswith(f"/{PHONE_NUMBER_ID}/messages")
    assert transport_call["payload"]["to"] == CUSTOMER_EXTERNAL_ID
    assert transport_call["payload"]["type"] == "text"
    assert transport_call["headers"] == {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(
            select(Workspace).where(Workspace.slug == workspace_slug)
        ).one()
        isolated_workspace = session.exec(
            select(Workspace).where(Workspace.slug == isolated_slug)
        ).one()
        configured_settings = app.dependency_overrides[get_settings]()
        native_adapter = OutboundIntegrationDeliveryService.from_settings(
            session,
            configured_settings,
        ).adapter_registry.get("whatsapp_cloud")
        assert isinstance(native_adapter, WhatsAppCloudDeliveryAdapter)

        receipt = session.exec(
            select(InboundIntegrationEventReceipt).where(
                InboundIntegrationEventReceipt.workspace_id == workspace.id,
                InboundIntegrationEventReceipt.external_event_id == direct_event_id,
            )
        ).one()
        identity = session.exec(
            select(InboundExternalIdentity).where(
                InboundExternalIdentity.workspace_id == workspace.id,
                InboundExternalIdentity.integration_account_id == UUID(account["id"]),
            )
        ).one()
        lead = session.exec(
            select(Lead).where(
                Lead.tenant_id == workspace_slug,
                Lead.phone == CUSTOMER_EXTERNAL_ID,
            )
        ).one()
        action = session.exec(
            select(OutboundIntegrationAction).where(
                OutboundIntegrationAction.workspace_id == workspace.id,
                OutboundIntegrationAction.integration_account_id == UUID(account["id"]),
            )
        ).one()
        attempt = session.exec(
            select(OutboundIntegrationDeliveryAttempt).where(
                OutboundIntegrationDeliveryAttempt.workspace_id == workspace.id,
                OutboundIntegrationDeliveryAttempt.outbound_integration_action_id
                == action.id,
            )
        ).one()
        audits = session.exec(
            select(OutboundIntegrationAuditEvent).where(
                OutboundIntegrationAuditEvent.workspace_id == workspace.id,
                OutboundIntegrationAuditEvent.outbound_integration_action_id
                == action.id,
            )
        ).all()
        work_items = session.exec(
            select(WorkItem).where(WorkItem.workspace_id == workspace.id)
        ).all()

        assert identity.lead_id == lead.id
        assert identity.contact_id == lead.contact_id
        assert receipt.integration_account_id == UUID(account["id"])
        assert action.status == OutboundIntegrationActionStatus.DELIVERED
        assert action.provider_delivery_id == provider_delivery_id
        assert attempt.provider_delivery_id == provider_delivery_id
        assert attempt.status == OutboundIntegrationActionStatus.DELIVERED
        assert {item.work_type for item in work_items} >= {
            "lead_capture",
            BusinessCapabilityKey.ANSWER_CUSTOMER.value,
            "sales_reply_message",
        }
        assert len(audits) == 3
        assert session.exec(
            select(InboundIntegrationEventReceipt).where(
                InboundIntegrationEventReceipt.workspace_id == isolated_workspace.id,
                InboundIntegrationEventReceipt.external_event_id == direct_event_id,
            )
        ).all() == []
        assert session.exec(
            select(InboundExternalIdentity).where(
                InboundExternalIdentity.workspace_id == isolated_workspace.id,
                InboundExternalIdentity.external_subject_id == CUSTOMER_EXTERNAL_ID,
            )
        ).all() == []

        def column_state(model) -> dict:
            return {
                attribute.key: getattr(model, attribute.key)
                for attribute in sa_inspect(model).mapper.column_attrs
            }

        persisted_state = json.dumps(
            {
                "receipt": column_state(receipt),
                "identity": column_state(identity),
                "lead": column_state(lead),
                "action": column_state(action),
                "attempt": column_state(attempt),
                "audits": [column_state(audit) for audit in audits],
                "work_items": [column_state(item) for item in work_items],
            },
            default=str,
            sort_keys=True,
        )
        action_id = action.id

    action_read = client.get(
        f"/api/integrations/outbound-actions/{action_id}",
        headers=_workspace_headers(workspace_slug),
    )
    isolated_read = client.get(
        f"/api/integrations/outbound-actions/{action_id}",
        headers=_workspace_headers(isolated_slug),
    )
    assert action_read.status_code == 200
    assert isolated_read.status_code == 404

    externally_visible = "\n".join(
        [first.text, duplicate.text, action_read.text, isolated_read.text, caplog.text]
    )
    for secret_value in (app_secret, access_token):
        assert secret_value not in persisted_state
        assert secret_value not in externally_visible


def test_direct_whatsapp_propagates_required_approval_without_delivery(
    client,
    monkeypatch,
):
    slug = "direct-wa-approval"
    app_secret_reference = "INTEGRATION_SECRET_DIRECT_WA_APPROVAL_APP_SECRET"
    app_secret = "direct-wa-approval-app-secret-value"
    _create_workspace(client, slug)
    account = _provision_account(
        client,
        slug,
        autonomy_level=AIEmployeeAutonomyLevel.DRAFT_REQUIRES_APPROVAL,
    )
    monkeypatch.setenv(app_secret_reference, app_secret)
    _configure_credential_reference(
        client,
        slug,
        account,
        "webhook_app_secret",
        app_secret_reference,
    )

    def fail_if_called(*args, **kwargs):
        del args, kwargs
        pytest.fail("approval-required WhatsApp send must not contact Meta")

    monkeypatch.setattr(HttpxWhatsAppCloudHttpTransport, "post", fail_if_called)
    headers, body = _signed_meta_request("valid_text.json", app_secret)
    response = client.post(
        f"{ENDPOINT}/{account['id']}",
        headers=headers,
        content=body,
    )

    assert response.status_code == 200
    assert response.json()["approval_id"] is not None
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        approval = session.get(ApprovalRequest, UUID(response.json()["approval_id"]))
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING
        approval_work_item = session.get(WorkItem, approval.work_item_id)
        workspace_id = session.exec(
            select(Workspace.id).where(Workspace.slug == slug)
        ).one()
        assert approval_work_item is not None
        assert approval_work_item.workspace_id == workspace_id
        assert session.exec(select(OutboundIntegrationAction)).all() == []


def test_direct_whatsapp_enforces_missing_tool_access_without_delivery(
    client,
    monkeypatch,
):
    slug = "direct-wa-tool-denied"
    app_secret_reference = "INTEGRATION_SECRET_DIRECT_WA_TOOL_DENIED_APP_SECRET"
    app_secret = "direct-wa-tool-denied-app-secret-value"
    _create_workspace(client, slug)
    account = _provision_account(client, slug, grant_tool_access=False)
    monkeypatch.setenv(app_secret_reference, app_secret)
    _configure_credential_reference(
        client,
        slug,
        account,
        "webhook_app_secret",
        app_secret_reference,
    )

    def fail_if_called(*args, **kwargs):
        del args, kwargs
        pytest.fail("tool-denied WhatsApp send must not contact Meta")

    monkeypatch.setattr(HttpxWhatsAppCloudHttpTransport, "post", fail_if_called)
    headers, body = _signed_meta_request("valid_text.json", app_secret)
    response = client.post(
        f"{ENDPOINT}/{account['id']}",
        headers=headers,
        content=body,
    )

    assert response.status_code == 200
    assert response.json()["approval_id"] is None
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        denied_send = session.exec(
            select(WorkItem).where(WorkItem.work_type == "sales_reply_message")
        ).one()
        assert denied_send.status == "failed"
        assert denied_send.error_code == "tool_access_denied"
        assert session.exec(select(OutboundIntegrationAction)).all() == []
