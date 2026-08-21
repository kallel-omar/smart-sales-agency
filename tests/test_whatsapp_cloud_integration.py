from uuid import UUID

import pytest
from sqlmodel import select

from app.core.ai_employees import AIEmployeeRoleKey
from app.core.ai_tool_access import AIEmployeeAutonomyLevel
from app.core.capabilities import BusinessCapabilityKey
from app.db import get_session
from app.main import app
from app.models import (
    AIEmployeeCapabilityAssignment,
    AIInvocationUsage,
    Capability,
    Contact,
    ConversationMessage,
    Department,
    InboundIntegrationEventReceipt,
    IntegrationAccount,
    Lead,
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
from app.services.lead_capture import LeadCaptureService

PHONE_NUMBER_ID = "555666777888999"
WRONG_PHONE_NUMBER_ID = "999888777666555"
CUSTOMER_EXTERNAL_ID = "15557654321"
EVENT_ID = "wamid.HBgLMTU1NTc2NTQzMjEVAgASGBQzQUMzRjA0N0Y2MzY2QzA0AA=="
ENDPOINT = "/api/integrations/inbound-events/whatsapp-cloud"


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
            AIEmployeeAutonomyLevel.CONTROLLED_AUTOMATION,
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
