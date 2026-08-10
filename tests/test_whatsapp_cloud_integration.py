from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import ConversationMessage, OutboundIntegrationAction, User

PHONE_NUMBER_ID = "555666777888999"
WRONG_PHONE_NUMBER_ID = "999888777666555"
CUSTOMER_EXTERNAL_ID = "15557654321"
EVENT_ID = "wamid.HBgLMTU1NTc2NTQzMjEVAgASGBQzQUMzRjA0N0Y2MzY2QzA0AA"
ENDPOINT = "/api/integrations/inbound-events/whatsapp-cloud"


def _workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _create_workspace(client, slug: str) -> None:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug})
    assert response.status_code == 201


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
    return response.json()


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
                select(ConversationMessage).where(
                    ConversationMessage.lead_id == UUID(lead_id)
                )
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
    assert _message_count(lead_id) == 1


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
