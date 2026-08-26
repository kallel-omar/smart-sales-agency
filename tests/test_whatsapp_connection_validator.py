from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlmodel import select

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import (
    AIEmployeeCapabilityToolAccess,
    ConversationMessage,
    IntegrationAccount,
    IntegrationAccountAuditAction,
    IntegrationAccountAuditEvent,
    IntegrationAccountConnectionStatus,
    Lead,
    OutboundIntegrationAction,
    User,
    WorkItem,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.services.authentication import AuthenticationService
from app.services.channel_connections import ChannelConnectionValidatorRegistry
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceService,
)
from app.services.whatsapp_connection_validator import (
    WhatsAppCloudConnectionValidator,
    WhatsAppCloudValidationHttpResponse,
)

integration_routes = importlib.import_module("app.api.routes.integrations")

PHONE_NUMBER_ID = "106540352242922"
TOKEN_REFERENCE = "INTEGRATION_SECRET_WHATSAPP_VALIDATOR_TEST"
ACCESS_TOKEN = "task-295c1-mocked-access-token"


class RecordingTransport:
    def __init__(
        self,
        response: WhatsAppCloudValidationHttpResponse | None = None,
        error: httpx.HTTPError | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict[str, str], httpx.Timeout]] = []

    def get(self, url, *, headers, timeout):
        self.calls.append((url, headers, timeout))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def workspace_headers(slug: str, token: str | None = None) -> dict[str, str]:
    headers = {"X-Workspace-Slug": slug}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def create_workspace(client, slug: str) -> UUID:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def provision_account(
    client,
    slug: str,
    *,
    provider: str = "whatsapp_cloud",
    external_account_id: str = PHONE_NUMBER_ID,
) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(slug),
        json={
            "provider": provider,
            "external_account_id": external_account_id,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    return response.json()


def attach_reference(
    client,
    slug: str,
    account_id: str,
    purpose: str,
    *,
    reference: str = TOKEN_REFERENCE,
    expires_at: datetime | None = None,
) -> None:
    payload: dict[str, str] = {"secret_reference": reference}
    if expires_at is not None:
        payload["expires_at"] = expires_at.isoformat()
    response = client.put(
        f"/api/integrations/accounts/{account_id}/credential-references/{purpose}",
        headers=workspace_headers(slug),
        json=payload,
    )
    assert response.status_code == 200


def install_validator(monkeypatch, transport: RecordingTransport, *, base_url=None, version=None):
    monkeypatch.setenv(TOKEN_REFERENCE, ACCESS_TOKEN)

    def factory(session, settings):
        validator = WhatsAppCloudConnectionValidator(
            IntegrationCredentialReferenceService(session),
            graph_api_base_url=base_url or settings.whatsapp_cloud_graph_api_base_url,
            graph_api_version=version or settings.whatsapp_cloud_graph_api_version,
            connect_timeout_seconds=settings.whatsapp_cloud_connect_timeout_seconds,
            read_timeout_seconds=settings.whatsapp_cloud_read_timeout_seconds,
            transport=transport,
        )
        return ChannelConnectionValidatorRegistry({"whatsapp_cloud": validator})

    monkeypatch.setattr(
        integration_routes,
        "default_channel_connection_validator_registry",
        factory,
    )


def successful_response(phone_number_id: str = PHONE_NUMBER_ID):
    return WhatsAppCloudValidationHttpResponse(
        status_code=200,
        headers={"facebook-api-version": "v23.0"},
        body={
            "id": phone_number_id,
            "code_verification_status": "VERIFIED",
            "verified_name": "Safe Test Business",
        },
    )


def validate(client, slug: str, account_id: str, *, token: str | None = None):
    return client.post(
        f"/api/integrations/accounts/{account_id}/validate-connection",
        headers=workspace_headers(slug, token),
    )


def persisted_counts() -> dict[str, int]:
    with next(app.dependency_overrides[get_session]()) as session:
        return {
            "work_items": len(session.exec(select(WorkItem)).all()),
            "outbound_actions": len(session.exec(select(OutboundIntegrationAction)).all()),
            "leads": len(session.exec(select(Lead)).all()),
            "messages": len(session.exec(select(ConversationMessage)).all()),
            "tool_grants": len(
                session.exec(select(AIEmployeeCapabilityToolAccess)).all()
            ),
        }


def test_successful_whatsapp_validation_is_read_only_safe_and_remains_inactive(
    client,
    monkeypatch,
):
    create_workspace(client, "validator-success")
    account = provision_account(client, "validator-success")
    for purpose in ("api_access_token", "webhook_app_secret", "webhook_verify_token"):
        attach_reference(client, "validator-success", account["id"], purpose)
    transport = RecordingTransport(successful_response())
    install_validator(monkeypatch, transport)
    before = persisted_counts()

    response = validate(client, "validator-success", account["id"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["succeeded"] is True
    assert payload["reason_code"] is None
    assert payload["provider_account_identity"] == PHONE_NUMBER_ID
    assert payload["account"]["connection_status"] == "connected"
    assert payload["account"]["active"] is False
    assert payload["account"]["last_validated_at"] is not None
    assert "provider_identity_matches" in payload["checks_passed"]
    assert "phone_number_verified" in payload["checks_passed"]
    assert "local_webhook_app_secret_configured" in payload["checks_passed"]
    assert "local_webhook_verify_token_configured" in payload["checks_passed"]
    assert "provider_webhook_subscription" in payload["checks_unavailable"]
    assert "whatsapp_business_account_identity" in payload["checks_unavailable"]
    assert "whatsapp_business_messaging_permission" in payload["checks_unavailable"]
    assert persisted_counts() == before

    assert len(transport.calls) == 1
    url, request_headers, timeout = transport.calls[0]
    assert url == (
        "https://graph.facebook.com/v23.0/106540352242922"
        "?fields=id,code_verification_status"
    )
    assert request_headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert ACCESS_TOKEN not in url
    assert timeout.connect == 5
    assert timeout.read == 15

    serialized = str(payload)
    assert ACCESS_TOKEN not in serialized
    assert TOKEN_REFERENCE not in serialized
    assert "Safe Test Business" not in serialized

    audit = client.get(
        f"/api/integrations/accounts/{account['id']}/audit-events",
        headers=workspace_headers("validator-success"),
    )
    assert audit.status_code == 200
    events = audit.json()
    actions = {event["action"] for event in events}
    assert "validation_succeeded" in actions
    assert "connected" in actions
    assert all(event["actor_user_id"] is not None for event in events)
    assert ACCESS_TOKEN not in str(events)
    assert TOKEN_REFERENCE not in str(events)

    health = client.get(
        f"/api/integrations/accounts/{account['id']}/health",
        headers=workspace_headers("validator-success"),
    )
    assert health.status_code == 200
    assert health.json()["connection_status"] == "connected"
    assert health.json()["health"] == "inactive"


def test_webhook_credentials_are_reported_but_do_not_block_phone_validation(
    client,
    monkeypatch,
):
    create_workspace(client, "validator-webhook-local")
    account = provision_account(client, "validator-webhook-local")
    attach_reference(client, "validator-webhook-local", account["id"], "api_access_token")
    transport = RecordingTransport(successful_response())
    install_validator(monkeypatch, transport)

    response = validate(client, "validator-webhook-local", account["id"])

    assert response.status_code == 200
    assert response.json()["succeeded"] is True
    assert set(response.json()["checks_failed"]) == {
        "local_webhook_app_secret_configured",
        "local_webhook_verify_token_configured",
    }
    assert response.json()["account"]["connection_status"] == "connected"


@pytest.mark.parametrize(
    ("response", "error", "expected_reason", "temporary"),
    [
        (
            WhatsAppCloudValidationHttpResponse(
                200,
                {},
                {"id": "different-phone-id", "code_verification_status": "VERIFIED"},
            ),
            None,
            "whatsapp_cloud_phone_number_id_mismatch",
            False,
        ),
        (
            WhatsAppCloudValidationHttpResponse(
                200,
                {},
                {"id": PHONE_NUMBER_ID, "code_verification_status": "NOT_VERIFIED"},
            ),
            None,
            "whatsapp_cloud_phone_number_not_verified",
            False,
        ),
        (
            WhatsAppCloudValidationHttpResponse(
                401,
                {},
                {"error": {"message": "raw revoked token detail", "code": 190}},
            ),
            None,
            "whatsapp_cloud_authentication_failed",
            False,
        ),
        (
            WhatsAppCloudValidationHttpResponse(
                400,
                {},
                {"error": {"message": "raw expired token detail", "code": 190}},
            ),
            None,
            "whatsapp_cloud_authentication_failed",
            False,
        ),
        (
            WhatsAppCloudValidationHttpResponse(
                403,
                {},
                {"error": {"message": "raw permission detail", "code": 10}},
            ),
            None,
            "whatsapp_cloud_permission_denied",
            False,
        ),
        (
            WhatsAppCloudValidationHttpResponse(429, {}, {"error": {"message": "raw limit"}}),
            None,
            "whatsapp_cloud_rate_limited",
            True,
        ),
        (
            WhatsAppCloudValidationHttpResponse(503, {}, {"error": {"message": "raw outage"}}),
            None,
            "whatsapp_cloud_provider_unavailable",
            True,
        ),
        (
            None,
            httpx.ConnectError(
                "raw network detail",
                request=httpx.Request("GET", "https://graph.facebook.com"),
            ),
            "whatsapp_cloud_network_error",
            True,
        ),
    ],
)
def test_initial_validation_failures_are_safe_and_preserve_configured_state(
    client,
    monkeypatch,
    response,
    error,
    expected_reason,
    temporary,
):
    slug = f"validator-{expected_reason[-18:].replace('_', '-')}"
    create_workspace(client, slug)
    account = provision_account(client, slug)
    attach_reference(client, slug, account["id"], "api_access_token")
    transport = RecordingTransport(response, error)
    install_validator(monkeypatch, transport)

    result = validate(client, slug, account["id"])

    assert result.status_code == 200
    payload = result.json()
    assert payload["succeeded"] is False
    assert payload["reason_code"] == expected_reason
    assert payload["temporary_failure"] is temporary
    assert payload["account"]["connection_status"] == "configured"
    assert payload["account"]["active"] is False
    assert payload["account"]["last_connection_error_code"] == expected_reason
    serialized = str(payload)
    assert ACCESS_TOKEN not in serialized
    assert TOKEN_REFERENCE not in serialized
    assert "raw " not in serialized

    audit = client.get(
        f"/api/integrations/accounts/{account['id']}/audit-events",
        headers=workspace_headers(slug),
    ).json()
    failed = next(event for event in audit if event["action"] == "validation_failed")
    assert failed["reason_code"] == expected_reason
    assert ACCESS_TOKEN not in str(audit)
    assert "raw " not in str(audit)


def test_missing_and_unresolved_access_token_fail_without_provider_request(
    client,
    monkeypatch,
):
    create_workspace(client, "validator-credential")
    account = provision_account(client, "validator-credential")
    transport = RecordingTransport(successful_response())
    install_validator(monkeypatch, transport)

    missing = validate(client, "validator-credential", account["id"])
    assert missing.status_code == 200
    assert missing.json()["reason_code"] == (
        "whatsapp_cloud_access_token_reference_missing"
    )
    assert transport.calls == []

    attach_reference(client, "validator-credential", account["id"], "api_access_token")
    monkeypatch.delenv(TOKEN_REFERENCE)
    unresolved = validate(client, "validator-credential", account["id"])
    assert unresolved.status_code == 200
    assert unresolved.json()["reason_code"] == "whatsapp_cloud_access_token_unavailable"
    assert unresolved.json()["account"]["connection_status"] == "configured"
    assert transport.calls == []


def test_expired_access_token_reference_fails_before_provider_request(client, monkeypatch):
    create_workspace(client, "validator-expired")
    account = provision_account(client, "validator-expired")
    attach_reference(
        client,
        "validator-expired",
        account["id"],
        "api_access_token",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    transport = RecordingTransport(successful_response())
    install_validator(monkeypatch, transport)

    response = validate(client, "validator-expired", account["id"])

    assert response.status_code == 200
    assert response.json()["reason_code"] == "whatsapp_cloud_access_token_expired"
    assert response.json()["account"]["connection_status"] == "configured"
    assert transport.calls == []


def test_established_auth_failure_requires_reconnect_but_transient_failure_does_not(
    client,
    monkeypatch,
):
    create_workspace(client, "validator-reconnect")
    account = provision_account(client, "validator-reconnect")
    attach_reference(client, "validator-reconnect", account["id"], "api_access_token")
    transport = RecordingTransport(successful_response())
    install_validator(monkeypatch, transport)
    assert validate(client, "validator-reconnect", account["id"]).json()["succeeded"]

    transport.response = WhatsAppCloudValidationHttpResponse(503, {}, None)
    transient = validate(client, "validator-reconnect", account["id"])
    assert transient.json()["temporary_failure"] is True
    assert transient.json()["account"]["connection_status"] == "connected"

    transport.response = WhatsAppCloudValidationHttpResponse(
        400,
        {},
        {"error": {"code": 190, "message": "raw revoked token"}},
    )
    auth = validate(client, "validator-reconnect", account["id"])
    assert auth.json()["reason_code"] == "whatsapp_cloud_authentication_failed"
    assert auth.json()["account"]["connection_status"] == "reconnect_required"
    assert auth.json()["account"]["active"] is False


def test_disconnected_cross_workspace_and_unsupported_provider_fail_closed(
    client,
    monkeypatch,
):
    create_workspace(client, "validator-scope-a")
    create_workspace(client, "validator-scope-b")
    account = provision_account(client, "validator-scope-a")
    generic = provision_account(
        client,
        "validator-scope-a",
        provider="generic_hmac",
        external_account_id="generic-validator",
    )
    attach_reference(client, "validator-scope-a", account["id"], "api_access_token")
    transport = RecordingTransport(successful_response())
    install_validator(monkeypatch, transport)

    cross_workspace = validate(client, "validator-scope-b", account["id"])
    assert cross_workspace.status_code == 404
    unsupported = validate(client, "validator-scope-a", generic["id"])
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"] == (
        "Connection validation is not supported for this provider"
    )

    disconnected = client.post(
        f"/api/integrations/accounts/{account['id']}/disconnect",
        headers=workspace_headers("validator-scope-a"),
    )
    assert disconnected.status_code == 200
    denied = validate(client, "validator-scope-a", account["id"])
    assert denied.status_code == 409
    assert "reconfigured before validation" in denied.json()["detail"]
    assert transport.calls == []


def test_non_manager_cannot_validate_or_mutate_connection_state(client, monkeypatch):
    workspace_id = create_workspace(client, "validator-permission")
    account = provision_account(client, "validator-permission")
    attach_reference(client, "validator-permission", account["id"], "api_access_token")
    transport = RecordingTransport(successful_response())
    install_validator(monkeypatch, transport)

    with next(app.dependency_overrides[get_session]()) as session:
        settings = app.dependency_overrides[get_settings]()
        auth = AuthenticationService(session, settings)
        user = auth.register(
            email="validator-member@example.com",
            password="validator-member-password",
        )
        session.add(
            WorkspaceMember(
                workspace_id=workspace_id,
                user_id=user.id,
                role=WorkspaceMemberRole.MEMBER,
            )
        )
        session.commit()
        token = auth.issue_access_token(user)

    denied = validate(client, "validator-permission", account["id"], token=token)

    assert denied.status_code == 403
    assert transport.calls == []
    with next(app.dependency_overrides[get_session]()) as session:
        stored = session.get(IntegrationAccount, UUID(account["id"]))
        assert stored is not None
        assert stored.connection_status == IntegrationAccountConnectionStatus.CONFIGURED
        assert stored.last_validated_at is None


def test_validator_rejects_non_whatsapp_account_and_non_allowlisted_host(client):
    create_workspace(client, "validator-direct")
    generic = provision_account(
        client,
        "validator-direct",
        provider="generic_hmac",
        external_account_id="generic-direct",
    )
    whatsapp = provision_account(
        client,
        "validator-direct",
        external_account_id="another-phone-id",
    )
    with next(app.dependency_overrides[get_session]()) as session:
        generic_account = session.get(IntegrationAccount, UUID(generic["id"]))
        whatsapp_account = session.get(IntegrationAccount, UUID(whatsapp["id"]))
        assert generic_account is not None and whatsapp_account is not None
        transport = RecordingTransport(successful_response("another-phone-id"))
        validator = WhatsAppCloudConnectionValidator(
            IntegrationCredentialReferenceService(session),
            graph_api_base_url="https://attacker.example",
            graph_api_version="v23.0",
            transport=transport,
        )

        provider_mismatch = validator.validate(generic_account)
        hostile_host = validator.validate(whatsapp_account)

    assert provider_mismatch.reason_code == "whatsapp_cloud_provider_mismatch"
    assert hostile_host.reason_code == "whatsapp_cloud_validator_configuration_invalid"
    assert transport.calls == []


def test_validation_success_audit_actor_matches_authenticated_user(client, monkeypatch):
    create_workspace(client, "validator-actor")
    account = provision_account(client, "validator-actor")
    attach_reference(client, "validator-actor", account["id"], "api_access_token")
    transport = RecordingTransport(successful_response())
    install_validator(monkeypatch, transport)

    response = validate(client, "validator-actor", account["id"])
    assert response.status_code == 200

    with next(app.dependency_overrides[get_session]()) as session:
        fixture_user = session.exec(select(User)).one()
        events = list(
            session.exec(
                select(IntegrationAccountAuditEvent).where(
                    IntegrationAccountAuditEvent.integration_account_id
                    == UUID(account["id"]),
                    IntegrationAccountAuditEvent.action
                    == IntegrationAccountAuditAction.VALIDATION_SUCCEEDED,
                )
            ).all()
        )
        assert len(events) == 1
        assert events[0].actor_user_id == fixture_user.id
        assert events[0].credential_purpose is None
        assert events[0].reason_code is None
