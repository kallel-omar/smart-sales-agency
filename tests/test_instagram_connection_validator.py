from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlmodel import select

from app.config import get_settings
from app.db import get_session
from app.integrations.providers import get_provider_requirements
from app.main import app
from app.models import (
    AIEmployeeCapabilityToolAccess,
    ConversationMessage,
    IntegrationAccount,
    IntegrationAccountAuditAction,
    IntegrationAccountAuditEvent,
    Lead,
    OutboundIntegrationAction,
    User,
    WorkItem,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.services.authentication import AuthenticationService
from app.services.channel_connections import (
    ChannelConnectionValidatorNotFoundError,
    ChannelConnectionValidatorRegistry,
)
from app.services.instagram_connection_validator import (
    InstagramNativeLoginConnectionValidator,
    InstagramValidationHttpResponse,
)
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceService,
)
from app.services.whatsapp_connection_validator import (
    default_channel_connection_validator_registry,
)

integration_routes = importlib.import_module("app.api.routes.integrations")

INSTAGRAM_ACCOUNT_ID = "17841439019937286"
TOKEN_REFERENCE = "INTEGRATION_SECRET_INSTAGRAM_VALIDATOR_TEST"
ACCESS_TOKEN = "task-295c2-mocked-access-token"


class RecordingTransport:
    def __init__(
        self,
        outcomes: list[InstagramValidationHttpResponse | httpx.HTTPError],
    ) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, str], httpx.Timeout]] = []

    def get(self, url, *, headers, timeout):
        self.calls.append((url, headers, timeout))
        assert self.outcomes
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, httpx.HTTPError):
            raise outcome
        return outcome


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
    provider_auth_mode: str | None = "instagram_login",
    external_account_id: str = INSTAGRAM_ACCOUNT_ID,
) -> dict:
    payload = {
        "provider": "instagram_dm",
        "external_account_id": external_account_id,
        "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
    }
    if provider_auth_mode is not None:
        payload["provider_auth_mode"] = provider_auth_mode
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(slug),
        json=payload,
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
        validator = InstagramNativeLoginConnectionValidator(
            IntegrationCredentialReferenceService(session),
            graph_api_base_url=base_url or settings.instagram_graph_api_base_url,
            graph_api_version=version or settings.meta_graph_api_version,
            connect_timeout_seconds=settings.meta_graph_connect_timeout_seconds,
            read_timeout_seconds=settings.meta_graph_read_timeout_seconds,
            transport=transport,
        )
        return ChannelConnectionValidatorRegistry(
            {("instagram_dm", "instagram_login"): validator}
        )

    monkeypatch.setattr(
        integration_routes,
        "default_channel_connection_validator_registry",
        factory,
    )


def identity_response(
    account_id: str = INSTAGRAM_ACCOUNT_ID,
    *,
    username: str | None = "hiri_hq",
) -> InstagramValidationHttpResponse:
    identity = {"user_id": account_id}
    if username is not None:
        identity["username"] = username
    return InstagramValidationHttpResponse(
        status_code=200,
        headers={"instagram-api-version": "v23.0"},
        body={"data": [identity]},
    )


def messaging_response() -> InstagramValidationHttpResponse:
    return InstagramValidationHttpResponse(
        status_code=200,
        headers={"instagram-api-version": "v23.0"},
        body={"data": [{"id": "raw-private-conversation-id"}]},
    )


def successful_transport() -> RecordingTransport:
    return RecordingTransport([identity_response(), messaging_response()])


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


def test_native_instagram_success_is_read_only_safe_connected_and_inactive(
    client,
    monkeypatch,
):
    create_workspace(client, "instagram-validator-success")
    account = provision_account(client, "instagram-validator-success")
    for purpose in ("api_access_token", "webhook_app_secret", "webhook_verify_token"):
        attach_reference(client, "instagram-validator-success", account["id"], purpose)
    transport = successful_transport()
    install_validator(monkeypatch, transport)
    before = persisted_counts()

    response = validate(client, "instagram-validator-success", account["id"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["succeeded"] is True
    assert payload["reason_code"] is None
    assert payload["provider_account_identity"] == INSTAGRAM_ACCOUNT_ID
    assert payload["account"]["provider_auth_mode"] == "instagram_login"
    assert payload["account"]["connection_status"] == "connected"
    assert payload["account"]["active"] is False
    assert payload["account"]["last_validated_at"] is not None
    assert "provider_identity_matches" in payload["checks_passed"]
    assert "instagram_business_basic_access" in payload["checks_passed"]
    assert "instagram_business_manage_messages_access" in payload["checks_passed"]
    assert "professional_account_username_available" in payload["checks_passed"]
    assert "local_webhook_app_secret_configured" in payload["checks_passed"]
    assert "local_webhook_verify_token_configured" in payload["checks_passed"]
    assert set(payload["checks_unavailable"]) == {
        "provider_webhook_subscription",
        "provider_webhook_subscription_fields",
    }
    assert persisted_counts() == before

    assert len(transport.calls) == 2
    identity_url, identity_headers, identity_timeout = transport.calls[0]
    assert identity_url == (
        "https://graph.instagram.com/v23.0/me?fields=user_id,username"
    )
    assert identity_headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert ACCESS_TOKEN not in identity_url
    assert identity_timeout.connect == 5
    assert identity_timeout.read == 15
    messaging_url, messaging_headers, _ = transport.calls[1]
    assert messaging_url == (
        "https://graph.instagram.com/v23.0/17841439019937286/"
        "conversations?platform=instagram"
    )
    assert messaging_headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert ACCESS_TOKEN not in messaging_url

    serialized = str(payload)
    assert ACCESS_TOKEN not in serialized
    assert TOKEN_REFERENCE not in serialized
    assert "hiri_hq" not in serialized
    assert "raw-private-conversation-id" not in serialized

    audit = client.get(
        f"/api/integrations/accounts/{account['id']}/audit-events",
        headers=workspace_headers("instagram-validator-success"),
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
        headers=workspace_headers("instagram-validator-success"),
    )
    assert health.status_code == 200
    assert health.json()["connection_status"] == "connected"
    assert health.json()["health"] == "inactive"


def test_local_webhook_readiness_is_nonblocking_and_subscription_is_unavailable(
    client,
    monkeypatch,
):
    create_workspace(client, "instagram-validator-webhook")
    account = provision_account(client, "instagram-validator-webhook")
    attach_reference(client, "instagram-validator-webhook", account["id"], "api_access_token")
    transport = successful_transport()
    install_validator(monkeypatch, transport)

    response = validate(client, "instagram-validator-webhook", account["id"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["succeeded"] is True
    assert set(payload["checks_failed"]) == {
        "local_webhook_app_secret_configured",
        "local_webhook_verify_token_configured",
    }
    assert "provider_webhook_subscription" in payload["checks_unavailable"]
    assert "provider_webhook_subscription_fields" in payload["checks_unavailable"]
    assert payload["account"]["connection_status"] == "connected"


@pytest.mark.parametrize(
    ("outcomes", "expected_reason", "temporary"),
    [
        (
            [identity_response("different-instagram-account")],
            "instagram_native_account_id_mismatch",
            False,
        ),
        (
            [
                InstagramValidationHttpResponse(
                    401,
                    {},
                    {"error": {"message": "raw revoked token", "code": 190}},
                )
            ],
            "instagram_native_authentication_failed",
            False,
        ),
        (
            [
                InstagramValidationHttpResponse(
                    400,
                    {},
                    {"error": {"message": "raw expired token", "code": 190}},
                )
            ],
            "instagram_native_authentication_failed",
            False,
        ),
        (
            [
                InstagramValidationHttpResponse(
                    403,
                    {},
                    {"error": {"message": "raw basic permission", "code": 10}},
                )
            ],
            "instagram_native_basic_permission_denied",
            False,
        ),
        (
            [
                identity_response(),
                InstagramValidationHttpResponse(
                    403,
                    {},
                    {"error": {"message": "raw messaging permission", "code": 10}},
                ),
            ],
            "instagram_native_messaging_permission_denied",
            False,
        ),
        (
            [InstagramValidationHttpResponse(429, {}, {"error": {"message": "raw limit"}})],
            "instagram_native_rate_limited",
            True,
        ),
        (
            [InstagramValidationHttpResponse(503, {}, {"error": {"message": "raw outage"}})],
            "instagram_native_provider_unavailable",
            True,
        ),
        (
            [
                httpx.ConnectError(
                    "raw network detail",
                    request=httpx.Request("GET", "https://graph.instagram.com"),
                )
            ],
            "instagram_native_network_error",
            True,
        ),
    ],
)
def test_initial_failures_are_safe_and_preserve_configured_state(
    client,
    monkeypatch,
    outcomes,
    expected_reason,
    temporary,
):
    slug = f"ig-{expected_reason[-24:].replace('_', '-')}"
    create_workspace(client, slug)
    account = provision_account(client, slug)
    attach_reference(client, slug, account["id"], "api_access_token")
    transport = RecordingTransport(outcomes)
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


def test_missing_unresolved_and_expired_access_token_do_not_call_provider(
    client,
    monkeypatch,
):
    create_workspace(client, "instagram-validator-credential")
    account = provision_account(client, "instagram-validator-credential")
    transport = successful_transport()
    install_validator(monkeypatch, transport)

    missing = validate(client, "instagram-validator-credential", account["id"])
    assert missing.status_code == 200
    assert missing.json()["reason_code"] == (
        "instagram_native_access_token_reference_missing"
    )
    assert transport.calls == []

    attach_reference(
        client,
        "instagram-validator-credential",
        account["id"],
        "api_access_token",
    )
    monkeypatch.delenv(TOKEN_REFERENCE)
    unresolved = validate(client, "instagram-validator-credential", account["id"])
    assert unresolved.status_code == 200
    assert unresolved.json()["reason_code"] == "instagram_native_access_token_unavailable"
    assert transport.calls == []

    create_workspace(client, "instagram-validator-expired")
    expired_account = provision_account(client, "instagram-validator-expired")
    attach_reference(
        client,
        "instagram-validator-expired",
        expired_account["id"],
        "api_access_token",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    monkeypatch.setenv(TOKEN_REFERENCE, ACCESS_TOKEN)
    expired = validate(client, "instagram-validator-expired", expired_account["id"])
    assert expired.status_code == 200
    assert expired.json()["reason_code"] == "instagram_native_access_token_expired"
    assert transport.calls == []


def test_established_auth_failure_reconnects_but_transient_failure_preserves_connected(
    client,
    monkeypatch,
):
    create_workspace(client, "instagram-validator-reconnect")
    account = provision_account(client, "instagram-validator-reconnect")
    attach_reference(client, "instagram-validator-reconnect", account["id"], "api_access_token")
    transport = RecordingTransport(
        [
            identity_response(),
            messaging_response(),
            InstagramValidationHttpResponse(503, {}, None),
            InstagramValidationHttpResponse(
                400,
                {},
                {"error": {"code": 190, "message": "raw revoked token"}},
            ),
        ]
    )
    install_validator(monkeypatch, transport)
    assert validate(client, "instagram-validator-reconnect", account["id"]).json()[
        "succeeded"
    ]

    transient = validate(client, "instagram-validator-reconnect", account["id"])
    assert transient.json()["temporary_failure"] is True
    assert transient.json()["account"]["connection_status"] == "connected"

    auth = validate(client, "instagram-validator-reconnect", account["id"])
    assert auth.json()["reason_code"] == "instagram_native_authentication_failed"
    assert auth.json()["account"]["connection_status"] == "reconnect_required"
    assert auth.json()["account"]["active"] is False


def test_disconnected_cross_workspace_and_non_native_modes_fail_closed(
    client,
    monkeypatch,
):
    create_workspace(client, "instagram-validator-scope-a")
    create_workspace(client, "instagram-validator-scope-b")
    native = provision_account(client, "instagram-validator-scope-a")
    facebook = provision_account(
        client,
        "instagram-validator-scope-a",
        provider_auth_mode="facebook_login",
        external_account_id="facebook-login-account",
    )
    legacy = provision_account(
        client,
        "instagram-validator-scope-a",
        provider_auth_mode=None,
        external_account_id="legacy-instagram-account",
    )
    assert legacy["provider_auth_mode"] == "facebook_login"
    attach_reference(client, "instagram-validator-scope-a", native["id"], "api_access_token")
    transport = successful_transport()
    install_validator(monkeypatch, transport)

    assert validate(client, "instagram-validator-scope-b", native["id"]).status_code == 404
    for account in (facebook, legacy):
        unsupported = validate(client, "instagram-validator-scope-a", account["id"])
        assert unsupported.status_code == 422
        assert unsupported.json()["detail"] == (
            "Connection validation is not supported for this provider"
        )

    disconnected = client.post(
        f"/api/integrations/accounts/{native['id']}/disconnect",
        headers=workspace_headers("instagram-validator-scope-a"),
    )
    assert disconnected.status_code == 200
    denied = validate(client, "instagram-validator-scope-a", native["id"])
    assert denied.status_code == 409
    assert "reconfigured before validation" in denied.json()["detail"]
    assert transport.calls == []


def test_integration_read_member_cannot_validate_but_manager_can(client, monkeypatch):
    workspace_id = create_workspace(client, "instagram-validator-permission")
    account = provision_account(client, "instagram-validator-permission")
    attach_reference(
        client,
        "instagram-validator-permission",
        account["id"],
        "api_access_token",
    )
    transport = successful_transport()
    install_validator(monkeypatch, transport)

    with next(app.dependency_overrides[get_session]()) as session:
        settings = app.dependency_overrides[get_settings]()
        auth = AuthenticationService(session, settings)
        user = auth.register(
            email="instagram-validator-member@example.com",
            password="instagram-validator-member-password",
        )
        session.add(
            WorkspaceMember(
                workspace_id=workspace_id,
                user_id=user.id,
                role=WorkspaceMemberRole.MEMBER,
            )
        )
        session.commit()
        member_token = auth.issue_access_token(user)

    denied = validate(
        client,
        "instagram-validator-permission",
        account["id"],
        token=member_token,
    )
    assert denied.status_code == 403
    assert transport.calls == []

    allowed = validate(client, "instagram-validator-permission", account["id"])
    assert allowed.status_code == 200
    assert allowed.json()["succeeded"] is True


def test_native_validator_rejects_wrong_provider_auth_mode_host_and_version(client):
    create_workspace(client, "instagram-validator-direct")
    native = provision_account(client, "instagram-validator-direct")
    facebook = provision_account(
        client,
        "instagram-validator-direct",
        provider_auth_mode="facebook_login",
        external_account_id="facebook-mode-direct",
    )
    with next(app.dependency_overrides[get_session]()) as session:
        native_account = session.get(IntegrationAccount, UUID(native["id"]))
        facebook_account = session.get(IntegrationAccount, UUID(facebook["id"]))
        assert native_account is not None and facebook_account is not None
        transport = successful_transport()
        hostile = InstagramNativeLoginConnectionValidator(
            IntegrationCredentialReferenceService(session),
            graph_api_base_url="https://attacker.example",
            graph_api_version="v23.0",
            transport=transport,
        )
        bad_version = InstagramNativeLoginConnectionValidator(
            IntegrationCredentialReferenceService(session),
            graph_api_base_url="https://graph.instagram.com",
            graph_api_version="v999.0",
            transport=transport,
        )

        wrong_mode = hostile.validate(facebook_account)
        hostile_host = hostile.validate(native_account)
        unsupported_version = bad_version.validate(native_account)

    assert wrong_mode.reason_code == "instagram_native_auth_mode_mismatch"
    assert hostile_host.reason_code == "instagram_native_validator_configuration_invalid"
    assert unsupported_version.reason_code == (
        "instagram_native_validator_configuration_invalid"
    )
    assert transport.calls == []


def test_registry_and_requirements_select_only_explicit_native_instagram_mode(client):
    validator = object()
    registry = ChannelConnectionValidatorRegistry(
        {("instagram_dm", "instagram_login"): validator}
    )

    assert registry.get("instagram_dm", "instagram_login") is validator
    with pytest.raises(ChannelConnectionValidatorNotFoundError):
        registry.get("instagram_dm", "facebook_login")
    with pytest.raises(ChannelConnectionValidatorNotFoundError):
        registry.get("instagram_dm")

    native_requirements = get_provider_requirements("instagram_dm", "instagram_login")
    facebook_requirements = get_provider_requirements("instagram_dm", None)
    assert native_requirements is not None
    assert native_requirements.validation_credential_purposes == {"api_access_token"}
    assert facebook_requirements is not None
    assert facebook_requirements.auth_mode == "facebook_login"
    assert facebook_requirements.validation_credential_purposes == set()

    with next(app.dependency_overrides[get_session]()) as session:
        settings = app.dependency_overrides[get_settings]()
        configured = default_channel_connection_validator_registry(session, settings)
        assert isinstance(
            configured.get("instagram_dm", "instagram_login"),
            InstagramNativeLoginConnectionValidator,
        )
        with pytest.raises(ChannelConnectionValidatorNotFoundError):
            configured.get("instagram_dm", "facebook_login")


def test_validation_success_audit_actor_matches_authenticated_user(client, monkeypatch):
    create_workspace(client, "instagram-validator-actor")
    account = provision_account(client, "instagram-validator-actor")
    attach_reference(client, "instagram-validator-actor", account["id"], "api_access_token")
    transport = successful_transport()
    install_validator(monkeypatch, transport)

    response = validate(client, "instagram-validator-actor", account["id"])
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
