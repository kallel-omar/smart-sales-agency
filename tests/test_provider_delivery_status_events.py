from datetime import datetime, timezone
from uuid import UUID

from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    OutboundIntegrationAction,
    OutboundIntegrationActionStatus,
    OutboundIntegrationAuditEvent,
    OutboundProviderDeliveryStatusEvent,
)

STATUS_ENDPOINT = "/api/integrations/inbound-events/provider-status-events"
PROVIDER_DELIVERY_ID = "wamid.HBgLMTU1NTc2NTQzMjEVAgASGBQzVEFTSzI4OVNUQVRVU0lEAA=="


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def provision_account(client, workspace_slug: str, *, provider: str = "whatsapp_cloud") -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(workspace_slug),
        json={
            "provider": provider,
            "external_account_id": f"{workspace_slug}-{provider}",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    return response.json()


def create_action(
    client,
    workspace_slug: str,
    account_id: str,
    *,
    key: str = "provider-status-action-1",
) -> dict:
    response = client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions",
        headers=workspace_headers(workspace_slug),
        json={
            "external_target_id": "recipient-123",
            "action_type": "send_message",
            "content": "Hello from Task 289",
            "payload": {"format": "plain_text"},
            "correlation_id": "conversation-t289",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()


def mark_action_delivered(action_id: str, provider_delivery_id: str = PROVIDER_DELIVERY_ID) -> None:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        action = session.get(OutboundIntegrationAction, UUID(action_id))
        assert action is not None
        action.status = OutboundIntegrationActionStatus.DELIVERED
        action.provider_delivery_id = provider_delivery_id
        action.delivered_at = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        action.failed_at = None
        action.failure_code = None
        action.failure_message = None
        action.failure_classification = None
        session.add(action)
        session.commit()


def status_payload(provider_status: str, **overrides) -> dict:
    payload = {
        "provider_delivery_id": PROVIDER_DELIVERY_ID,
        "provider_status": provider_status,
        "provider_timestamp": 1_722_000_000,
    }
    payload.update(overrides)
    return payload


def post_status(client, signed_webhook_request, account: dict, payload: dict):
    headers, body = signed_webhook_request(
        account["inbound_credential"],
        payload,
        event_id=f"{payload.get('provider_delivery_id', 'missing')}:{payload.get('provider_status', 'missing')}",
    )
    return client.post(STATUS_ENDPOINT, headers=headers, content=body)


def read_status_events(client, workspace_slug: str, account_id: str, action_id: str):
    return client.get(
        f"/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/provider-status-events",
        headers=workspace_headers(workspace_slug),
    )


def persisted_counts() -> tuple[int, int]:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        event_count = len(session.exec(select(OutboundProviderDeliveryStatusEvent)).all())
        audit_count = len(session.exec(select(OutboundIntegrationAuditEvent)).all())
    return event_count, audit_count


def force_status_event_created_at(
    action_id: str,
    created_at: datetime,
) -> list[tuple[str, str]]:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        events = list(
            session.exec(
                select(OutboundProviderDeliveryStatusEvent).where(
                    OutboundProviderDeliveryStatusEvent.outbound_integration_action_id
                    == UUID(action_id)
                )
            ).all()
        )
        for event in events:
            event.created_at = created_at
            session.add(event)
        session.commit()
        return sorted((str(event.id), event.provider_status.value) for event in events)


def test_sent_delivered_and_read_callbacks_persist_as_scoped_history(
    client,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    mark_action_delivered(action["id"])

    for provider_status in ("sent", "delivered", "read"):
        response = post_status(
            client,
            signed_webhook_request,
            account,
            status_payload(provider_status),
        )
        assert response.status_code == 200
        assert response.json()["duplicate"] is False

    response = read_status_events(client, "company-a", account["id"], action["id"])

    assert response.status_code == 200
    events = response.json()
    assert [event["provider_status"] for event in events] == ["sent", "delivered", "read"]
    assert {event["provider_delivery_id"] for event in events} == {PROVIDER_DELIVERY_ID}
    assert {
        "id",
        "workspace_id",
        "integration_account_id",
        "outbound_integration_action_id",
        "provider_delivery_id",
        "provider_status",
        "provider_timestamp",
        "provider_error_code",
        "provider_error_title",
        "provider_error_type",
        "failure_classification",
        "created_at",
    } == set(events[0])


def test_provider_status_read_uses_provider_chronology_when_arrival_order_differs(
    client,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    mark_action_delivered(action["id"])

    delivered = post_status(
        client,
        signed_webhook_request,
        account,
        status_payload("delivered", provider_timestamp=1_722_000_001),
    )
    sent = post_status(
        client,
        signed_webhook_request,
        account,
        status_payload("sent", provider_timestamp=1_722_000_000),
    )

    assert delivered.status_code == 200
    assert sent.status_code == 200
    response = read_status_events(client, "company-a", account["id"], action["id"])
    events = response.json()
    assert [event["provider_status"] for event in events] == ["sent", "delivered"]


def test_identical_provider_timestamps_use_created_at_and_id_tie_breakers(
    client,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    mark_action_delivered(action["id"])

    for provider_status in ("read", "sent", "delivered"):
        response = post_status(
            client,
            signed_webhook_request,
            account,
            status_payload(provider_status, provider_timestamp=1_722_000_000),
        )
        assert response.status_code == 200

    expected_order = force_status_event_created_at(
        action["id"],
        datetime(2026, 8, 11, 14, 53, 36, tzinfo=timezone.utc),
    )
    response = read_status_events(client, "company-a", account["id"], action["id"])
    events = response.json()

    assert [(event["id"], event["provider_status"]) for event in events] == expected_order


def test_failed_callback_persists_only_safe_failure_metadata(client, signed_webhook_request):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    mark_action_delivered(action["id"])

    response = post_status(
        client,
        signed_webhook_request,
        account,
        status_payload(
            "failed",
            provider_error_code="131026",
            provider_error_title="Message undeliverable",
            provider_error_type="OAuthException",
            failure_classification="permanent",
        ),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["event"]["provider_status"] == "failed"
    assert data["event"]["provider_error_code"] == "131026"
    assert data["event"]["provider_error_title"] == "Message undeliverable"
    assert data["event"]["provider_error_type"] == "OAuthException"
    assert data["event"]["failure_classification"] == "permanent"
    serialized = str(data).lower()
    for forbidden in ("authorization", "access_token", "signature", "raw_payload"):
        assert forbidden not in serialized

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        event = session.exec(select(OutboundProviderDeliveryStatusEvent)).one()
        assert not hasattr(event, "raw_payload")


def test_duplicate_callback_is_idempotent_and_does_not_duplicate_audit(
    client,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    mark_action_delivered(action["id"])
    payload = status_payload("delivered")

    before_events, before_audits = persisted_counts()
    first = post_status(client, signed_webhook_request, account, payload)
    duplicate = post_status(client, signed_webhook_request, account, payload)
    after_events, after_audits = persisted_counts()

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert first.json()["duplicate"] is False
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["event"]["id"] == first.json()["event"]["id"]
    assert after_events == before_events + 1
    assert after_audits == before_audits


def test_unknown_provider_delivery_id_fails_safely_without_history(
    client,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    mark_action_delivered(action["id"])

    response = post_status(
        client,
        signed_webhook_request,
        account,
        status_payload("sent", provider_delivery_id="wamid.unknown-task289-id"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Outbound integration action not found"
    assert persisted_counts()[0] == 0


def test_wrong_account_and_cross_workspace_cannot_correlate_provider_delivery_id(
    client,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    account_a = provision_account(client, "company-a")
    account_b = provision_account(client, "company-b")
    action_a = create_action(client, "company-a", account_a["id"])
    mark_action_delivered(action_a["id"])

    wrong_account = post_status(
        client,
        signed_webhook_request,
        account_b,
        status_payload("sent"),
    )
    cross_workspace_read = read_status_events(
        client,
        "company-b",
        account_a["id"],
        action_a["id"],
    )

    assert wrong_account.status_code == 404
    assert wrong_account.json()["detail"] == "Outbound integration action not found"
    assert cross_workspace_read.status_code == 404
    assert cross_workspace_read.json()["detail"] == "Integration account not found"
    assert persisted_counts()[0] == 0


def test_out_of_order_or_failed_callbacks_do_not_regress_action_state(
    client,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    mark_action_delivered(action["id"])

    assert (
        post_status(client, signed_webhook_request, account, status_payload("read")).status_code
        == 200
    )
    assert (
        post_status(client, signed_webhook_request, account, status_payload("sent")).status_code
        == 200
    )
    assert (
        post_status(
            client,
            signed_webhook_request,
            account,
            status_payload("failed", provider_error_code="131026"),
        ).status_code
        == 200
    )

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        persisted = session.get(OutboundIntegrationAction, UUID(action["id"]))
        assert persisted is not None
        assert persisted.status == OutboundIntegrationActionStatus.DELIVERED
        assert persisted.provider_delivery_id == PROVIDER_DELIVERY_ID
        assert persisted.delivered_at is not None
        assert persisted.failure_code is None

    response = read_status_events(client, "company-a", account["id"], action["id"])
    assert [event["provider_status"] for event in response.json()] == [
        "read",
        "sent",
        "failed",
    ]


def test_malformed_status_and_untrusted_workspace_fields_are_rejected(
    client,
    signed_webhook_request,
):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a")
    action = create_action(client, "company-a", account["id"])
    mark_action_delivered(action["id"])

    malformed = post_status(
        client,
        signed_webhook_request,
        account,
        status_payload("queued"),
    )
    with_workspace_authority = post_status(
        client,
        signed_webhook_request,
        account,
        status_payload("sent", workspace_id="forged", integration_account_id=account["id"]),
    )
    whitespace_id = post_status(
        client,
        signed_webhook_request,
        account,
        status_payload("sent", provider_delivery_id="wamid.invalid id"),
    )

    assert malformed.status_code == 422
    assert with_workspace_authority.status_code == 422
    assert whitespace_id.status_code == 422
    assert persisted_counts()[0] == 0
