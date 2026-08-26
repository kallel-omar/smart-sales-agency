from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import (
    AIEmployeeCapabilityToolAccess,
    IntegrationAccount,
    IntegrationAccountAuditEvent,
    IntegrationAccountConnectionStatus,
    OutboundDeliveryFailureClassification,
    OutboundIntegrationAction,
    User,
    Workspace,
)
from app.services.channel_connections import (
    ChannelConnectionLifecycleError,
    ChannelConnectionService,
    ChannelConnectionValidationResult,
)
from app.services.delivery_adapters import DeliveryAdapterRegistry, DeliveryAdapterResult
from app.services.outbound_delivery import (
    IntegrationAccountReconnectRequiredError,
    OutboundIntegrationDeliveryService,
)
from app.services.outbound_retry_policy import OutboundDeliveryRetryPolicy


class FakeValidator:
    def __init__(self, result: ChannelConnectionValidationResult) -> None:
        self.result = result
        self.account_ids: list[UUID] = []

    def validate(self, account: IntegrationAccount) -> ChannelConnectionValidationResult:
        self.account_ids.append(account.id)
        return self.result


class ResultAdapter:
    def __init__(self, result: DeliveryAdapterResult) -> None:
        self.result = result

    def deliver(self, action, account) -> DeliveryAdapterResult:
        del action, account
        return self.result


def headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def provision_account(
    client,
    slug: str,
    *,
    provider: str = "generic_hmac",
    external_account_id: str | None = None,
    provider_auth_mode: str | None = None,
) -> dict:
    payload = {
        "provider": provider,
        "external_account_id": external_account_id or f"{slug}-{provider}",
        "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
    }
    if provider_auth_mode is not None:
        payload["provider_auth_mode"] = provider_auth_mode
    response = client.post(
        "/api/integrations/accounts",
        headers=headers(slug),
        json=payload,
    )
    assert response.status_code == 201
    return response.json()


def scoped_state(slug: str, account_id: str):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        user = session.exec(select(User)).one()
        account = session.get(IntegrationAccount, UUID(account_id))
        assert account is not None
        yield session, workspace, user, account


def connect_account(slug: str, account_id: str) -> None:
    for session, workspace, user, _account in scoped_state(slug, account_id):
        validator = FakeValidator(ChannelConnectionValidationResult(succeeded=True))
        connected = ChannelConnectionService(session).validate(
            workspace,
            UUID(account_id),
            validator,
            actor_user_id=user.id,
        )
        assert connected.connection_status == IntegrationAccountConnectionStatus.CONNECTED
        assert validator.account_ids == [UUID(account_id)]


def connect_and_enable(client, slug: str, account_id: str) -> dict:
    connect_account(slug, account_id)
    response = client.post(
        f"/api/integrations/accounts/{account_id}/reactivate",
        headers=headers(slug),
    )
    assert response.status_code == 200
    return response.json()


def create_action(client, slug: str, account_id: str, key: str) -> dict:
    response = client.post(
        f"/api/integrations/accounts/{account_id}/outbound-actions",
        headers=headers(slug),
        json={
            "external_target_id": "recipient",
            "action_type": "send_message",
            "content": "Safe test message",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_configured_connected_and_active_lifecycles_remain_distinct(client):
    create_workspace(client, "lifecycle-a")
    account = provision_account(client, "lifecycle-a")
    assert account["connection_status"] == "configured"
    assert account["active"] is True
    assert account["last_validated_at"] is None

    disabled = client.post(
        f"/api/integrations/accounts/{account['id']}/deactivate",
        headers=headers("lifecycle-a"),
    )
    assert disabled.status_code == 200
    assert disabled.json()["active"] is False
    assert disabled.json()["connection_status"] == "configured"

    connect_account("lifecycle-a", account["id"])
    connected = client.get(
        "/api/integrations/accounts",
        headers=headers("lifecycle-a"),
    ).json()[0]
    assert connected["connection_status"] == "connected"
    assert connected["active"] is False
    assert connected["last_validated_at"] is not None

    connect_account("lifecycle-a", account["id"])
    still_disabled = client.get(
        "/api/integrations/accounts",
        headers=headers("lifecycle-a"),
    ).json()[0]
    assert still_disabled["active"] is False

    enabled = client.post(
        f"/api/integrations/accounts/{account['id']}/reactivate",
        headers=headers("lifecycle-a"),
    )
    assert enabled.status_code == 200
    assert enabled.json()["active"] is True
    assert enabled.json()["connection_status"] == "connected"


def test_new_customer_channel_requires_validation_before_execution(client):
    create_workspace(client, "validation-gate-a")
    account = provision_account(
        client,
        "validation-gate-a",
        provider="whatsapp_cloud",
        external_account_id="new-phone-number-id",
    )
    assert account["connection_status"] == "configured"
    assert account["active"] is False
    blocked = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=headers("validation-gate-a"),
        json={
            "external_target_id": "recipient",
            "action_type": "send_message",
            "content": "Safe test message",
            "idempotency_key": "blocked-before-validation",
        },
    )
    assert blocked.status_code == 409

    connect_and_enable(client, "validation-gate-a", account["id"])
    action = create_action(
        client,
        "validation-gate-a",
        account["id"],
        "allowed-after-validation",
    )
    for session, workspace, _user, stored_account in scoped_state(
        "validation-gate-a", account["id"]
    ):
        delivered, _ = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry(
                {
                    "whatsapp_cloud": ResultAdapter(
                        DeliveryAdapterResult.success("provider-delivery-id")
                    )
                }
            ),
        ).deliver_pending_action(workspace, stored_account.id, UUID(action["id"]))
        assert delivered.status.value == "delivered"


def test_historical_active_configured_account_retains_execution_compatibility(client):
    create_workspace(client, "legacy-compat-a")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.exec(
            select(Workspace).where(Workspace.slug == "legacy-compat-a")
        ).one()
        account = IntegrationAccount(
            workspace_id=workspace.id,
            provider="whatsapp_cloud",
            external_account_id="historical-phone-number-id",
            secret_reference="INTEGRATION_SECRET_GENERIC_HMAC_TEST",
            credential_hash="a" * 64,
            active=True,
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        account_id = str(account.id)

    action = create_action(
        client,
        "legacy-compat-a",
        account_id,
        "legacy-configured-action",
    )
    for session, workspace, _user, stored_account in scoped_state(
        "legacy-compat-a", account_id
    ):
        delivered, _ = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry(
                {
                    "whatsapp_cloud": ResultAdapter(
                        DeliveryAdapterResult.success("legacy-provider-delivery-id")
                    )
                }
            ),
        ).deliver_pending_action(workspace, stored_account.id, UUID(action["id"]))
        assert stored_account.connection_status == (
            IntegrationAccountConnectionStatus.CONFIGURED
        )
        assert delivered.status.value == "delivered"


def test_reconnect_required_blocks_delivery_and_validation_completes_reconnect(client):
    create_workspace(client, "reconnect-a")
    account = provision_account(client, "reconnect-a")
    connect_account("reconnect-a", account["id"])
    action = create_action(client, "reconnect-a", account["id"], "reconnect-action")

    for session, workspace, user, _account in scoped_state("reconnect-a", account["id"]):
        reconnect = ChannelConnectionService(session).mark_reconnect_required(
            workspace,
            UUID(account["id"]),
            reason_code="provider_authentication_failed",
            actor_user_id=user.id,
        )
        assert reconnect.active is True
        assert reconnect.reconnect_required_at is not None
        assert reconnect.last_connection_error_code == "provider_authentication_failed"

    blocked = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver",
        headers=headers("reconnect-a"),
    )
    assert blocked.status_code == 409
    assert "operator attention" in blocked.json()["detail"]

    connect_account("reconnect-a", account["id"])
    restored = client.get(
        "/api/integrations/accounts",
        headers=headers("reconnect-a"),
    ).json()[0]
    assert restored["connection_status"] == "connected"
    assert restored["reconnect_required_at"] is None
    assert restored["last_connection_error_code"] is None


def test_transient_validation_failure_does_not_require_reconnect(client):
    create_workspace(client, "validation-a")
    account = provision_account(client, "validation-a")
    connect_account("validation-a", account["id"])

    for session, workspace, user, _account in scoped_state("validation-a", account["id"]):
        validator = FakeValidator(
            ChannelConnectionValidationResult(
                succeeded=False,
                reason_code="provider_temporarily_unavailable",
                reconnect_required=False,
            )
        )
        result = ChannelConnectionService(session).validate(
            workspace,
            UUID(account["id"]),
            validator,
            actor_user_id=user.id,
        )
        assert result.connection_status == IntegrationAccountConnectionStatus.CONNECTED
        assert result.reconnect_required_at is None
        assert result.last_connection_error_code == "provider_temporarily_unavailable"


def test_authentication_delivery_failure_marks_reconnect_and_suppresses_retry(client):
    create_workspace(client, "delivery-auth-a")
    account = provision_account(client, "delivery-auth-a")
    connect_account("delivery-auth-a", account["id"])
    action = create_action(client, "delivery-auth-a", account["id"], "auth-action")

    result = DeliveryAdapterResult.failure(
        "authentication_failed",
        "Provider authentication failed",
        OutboundDeliveryFailureClassification.AUTHENTICATION,
    )
    for session, workspace, _user, stored_account in scoped_state(
        "delivery-auth-a", account["id"]
    ):
        service = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry(
                {stored_account.provider: ResultAdapter(result)}
            ),
        )
        failed, _ = service.deliver_pending_action(
            workspace,
            stored_account.id,
            UUID(action["id"]),
        )
        assert failed.failure_classification == (
            OutboundDeliveryFailureClassification.AUTHENTICATION
        )
        session.refresh(stored_account)
        assert stored_account.connection_status == (
            IntegrationAccountConnectionStatus.RECONNECT_REQUIRED
        )
        with pytest.raises(IntegrationAccountReconnectRequiredError):
            service.retry_failed_action(workspace, stored_account.id, failed.id)

    retry = OutboundDeliveryRetryPolicy(3).evaluate(
        attempt_count=1,
        failure_code="authentication_failed",
        failure_classification=OutboundDeliveryFailureClassification.AUTHENTICATION,
    )
    assert retry.allowed is False
    assert retry.denial_reason == "authentication_failure_requires_reconnect"


def test_temporary_delivery_failure_keeps_connected_state(client):
    create_workspace(client, "delivery-temp-a")
    account = provision_account(client, "delivery-temp-a")
    connect_account("delivery-temp-a", account["id"])
    action = create_action(client, "delivery-temp-a", account["id"], "temporary-action")

    result = DeliveryAdapterResult.failure(
        "temporary_failure",
        "Provider temporarily unavailable",
        OutboundDeliveryFailureClassification.TEMPORARY,
    )
    for session, workspace, _user, stored_account in scoped_state(
        "delivery-temp-a", account["id"]
    ):
        service = OutboundIntegrationDeliveryService(
            session,
            adapter_registry=DeliveryAdapterRegistry(
                {stored_account.provider: ResultAdapter(result)}
            ),
        )
        service.deliver_pending_action(
            workspace,
            stored_account.id,
            UUID(action["id"]),
        )
        session.refresh(stored_account)
        assert stored_account.connection_status == IntegrationAccountConnectionStatus.CONNECTED
        assert stored_account.reconnect_required_at is None


def test_credential_purpose_expiry_and_api_output_are_safe(client):
    create_workspace(client, "credentials-a")
    account = provision_account(
        client,
        "credentials-a",
        provider="whatsapp_cloud",
        external_account_id="phone-number-id-a",
    )
    expiry = datetime.now(UTC) + timedelta(days=10)

    denied = client.put(
        f"/api/integrations/accounts/{account['id']}/credential-references/database_password",
        headers=headers("credentials-a"),
        json={"secret_reference": "INTEGRATION_SECRET_DATABASE_PASSWORD"},
    )
    assert denied.status_code == 422

    created = client.put(
        f"/api/integrations/accounts/{account['id']}/credential-references/api_access_token",
        headers=headers("credentials-a"),
        json={
            "secret_reference": "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
            "expires_at": expiry.isoformat(),
        },
    )
    assert created.status_code == 200
    assert created.json()["expires_at"] is not None
    serialized = str(created.json())
    assert "secret_reference" not in serialized
    assert "INTEGRATION_SECRET_WHATSAPP_API_TOKEN" not in serialized

    events = client.get(
        f"/api/integrations/accounts/{account['id']}/audit-events",
        headers=headers("credentials-a"),
    ).json()
    credential_event = next(
        event for event in events if event["action"] == "credential_reference_changed"
    )
    assert credential_event["credential_purpose"] == "api_access_token"
    assert credential_event["reason_code"] == "credential_reference_created"
    assert credential_event["actor_user_id"] is not None
    assert "INTEGRATION_SECRET" not in str(events)


def test_disconnect_removes_references_but_preserves_actions_and_audit(client):
    create_workspace(client, "disconnect-a")
    account = provision_account(client, "disconnect-a")
    action = create_action(client, "disconnect-a", account["id"], "disconnect-action")
    reference = client.put(
        f"/api/integrations/accounts/{account['id']}/credential-references/api_access_token",
        headers=headers("disconnect-a"),
        json={"secret_reference": "INTEGRATION_SECRET_DISCONNECT_TOKEN"},
    )
    assert reference.status_code == 200

    disconnected = client.post(
        f"/api/integrations/accounts/{account['id']}/disconnect",
        headers=headers("disconnect-a"),
    )
    assert disconnected.status_code == 200
    assert disconnected.json()["connection_status"] == "disconnected"
    assert disconnected.json()["active"] is False
    assert client.get(
        f"/api/integrations/accounts/{account['id']}/credential-references",
        headers=headers("disconnect-a"),
    ).json() == []

    for session, _workspace, _user, stored_account in scoped_state(
        "disconnect-a", account["id"]
    ):
        assert stored_account.secret_reference is None
        assert session.get(OutboundIntegrationAction, UUID(action["id"])) is not None
        events = list(
            session.exec(
                select(IntegrationAccountAuditEvent).where(
                    IntegrationAccountAuditEvent.integration_account_id
                    == stored_account.id
                )
            ).all()
        )
        assert any(event.action.value == "disconnected" for event in events)
        assert not session.exec(select(AIEmployeeCapabilityToolAccess)).all()

    reconfigured = provision_account(
        client,
        "disconnect-a",
        external_account_id="disconnect-a-generic_hmac",
    )
    assert reconfigured["id"] == account["id"]
    assert reconfigured["connection_status"] == "configured"
    assert reconfigured["active"] is False
    assert client.put(
        f"/api/integrations/accounts/{account['id']}/credential-references/api_access_token",
        headers=headers("disconnect-a"),
        json={"secret_reference": "INTEGRATION_SECRET_RECONNECTED_TOKEN"},
    ).status_code == 200
    connect_and_enable(client, "disconnect-a", account["id"])
    for session, _workspace, _user, _stored_account in scoped_state(
        "disconnect-a", account["id"]
    ):
        assert session.get(OutboundIntegrationAction, UUID(action["id"])) is not None


def test_disconnect_and_reconnect_states_cannot_be_enabled_directly(client):
    create_workspace(client, "enable-guard-a")
    account = provision_account(client, "enable-guard-a")
    assert client.post(
        f"/api/integrations/accounts/{account['id']}/deactivate",
        headers=headers("enable-guard-a"),
    ).status_code == 200
    configured_enable = client.post(
        f"/api/integrations/accounts/{account['id']}/reactivate",
        headers=headers("enable-guard-a"),
    )
    assert configured_enable.status_code == 409

    disconnected = client.post(
        f"/api/integrations/accounts/{account['id']}/disconnect",
        headers=headers("enable-guard-a"),
    )
    assert disconnected.status_code == 200
    disconnected_enable = client.post(
        f"/api/integrations/accounts/{account['id']}/reactivate",
        headers=headers("enable-guard-a"),
    )
    assert disconnected_enable.status_code == 409

    for session, workspace, _user, _account in scoped_state(
        "enable-guard-a", account["id"]
    ):
        with pytest.raises(ChannelConnectionLifecycleError):
            ChannelConnectionService(session).validate(
                workspace,
                UUID(account["id"]),
                FakeValidator(ChannelConnectionValidationResult(succeeded=True)),
            )


def test_active_provider_identity_ownership_preserves_inactive_history(client):
    create_workspace(client, "ownership-a")
    create_workspace(client, "ownership-b")
    first = provision_account(
        client,
        "ownership-a",
        provider="whatsapp_cloud",
        external_account_id="shared-phone-number-id",
    )
    connect_and_enable(client, "ownership-a", first["id"])
    second = client.post(
        "/api/integrations/accounts",
        headers=headers("ownership-b"),
        json={
            "provider": "whatsapp_cloud",
            "external_account_id": "shared-phone-number-id",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert second.status_code == 201
    assert second.json()["active"] is False
    connect_account("ownership-b", second.json()["id"])
    conflict = client.post(
        f"/api/integrations/accounts/{second.json()['id']}/reactivate",
        headers=headers("ownership-b"),
    )
    assert conflict.status_code == 409

    assert client.post(
        f"/api/integrations/accounts/{first['id']}/deactivate",
        headers=headers("ownership-a"),
    ).status_code == 200
    replacement = client.post(
        f"/api/integrations/accounts/{second.json()['id']}/reactivate",
        headers=headers("ownership-b"),
    )
    assert replacement.status_code == 200
    assert replacement.json()["workspace_id"] != first["workspace_id"]


def test_existing_instagram_default_and_workspace_isolation_remain_compatible(client):
    create_workspace(client, "compat-a")
    create_workspace(client, "compat-b")
    account = provision_account(
        client,
        "compat-a",
        provider="instagram_dm",
        external_account_id="instagram-professional-id",
    )
    assert account["provider_auth_mode"] == "facebook_login"
    assert account["connection_status"] == "configured"

    denied = client.post(
        f"/api/integrations/accounts/{account['id']}/disconnect",
        headers=headers("compat-b"),
    )
    assert denied.status_code == 404
