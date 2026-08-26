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
from app.services.channel_connections import ChannelConnectionValidatorRegistry
from app.services.integration_credential_references import (
    IntegrationCredentialReferenceService,
)
from app.services.messenger_connection_validator import (
    FacebookMessengerConnectionValidator,
    MessengerValidationHttpResponse,
)
from app.services.whatsapp_connection_validator import (
    default_channel_connection_validator_registry,
)

integration_routes = importlib.import_module("app.api.routes.integrations")

PAGE_ID = "1302062409649643"
TOKEN_REFERENCE = "INTEGRATION_SECRET_MESSENGER_VALIDATOR_TEST"
ACCESS_TOKEN = "task-295c3-mocked-page-access-token"


class RecordingTransport:
    def __init__(
        self,
        outcomes: list[MessengerValidationHttpResponse | httpx.HTTPError],
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


def provision_account(client, slug: str, *, page_id: str = PAGE_ID) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(slug),
        json={
            "provider": "facebook_messenger",
            "external_account_id": page_id,
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
    expires_at: datetime | None = None,
) -> None:
    payload: dict[str, str] = {"secret_reference": TOKEN_REFERENCE}
    if expires_at is not None:
        payload["expires_at"] = expires_at.isoformat()
    response = client.put(
        f"/api/integrations/accounts/{account_id}/credential-references/{purpose}",
        headers=workspace_headers(slug),
        json=payload,
    )
    assert response.status_code == 200


def install_validator(
    monkeypatch,
    transport: RecordingTransport,
    *,
    base_url: str | None = None,
    version: str | None = None,
) -> None:
    monkeypatch.setenv(TOKEN_REFERENCE, ACCESS_TOKEN)

    def factory(session, settings):
        return ChannelConnectionValidatorRegistry(
            {
                "facebook_messenger": FacebookMessengerConnectionValidator(
                    IntegrationCredentialReferenceService(session),
                    graph_api_base_url=base_url or settings.meta_graph_api_base_url,
                    graph_api_version=version or settings.meta_graph_api_version,
                    connect_timeout_seconds=settings.meta_graph_connect_timeout_seconds,
                    read_timeout_seconds=settings.meta_graph_read_timeout_seconds,
                    transport=transport,
                )
            }
        )

    monkeypatch.setattr(
        integration_routes,
        "default_channel_connection_validator_registry",
        factory,
    )


def identity_response(page_id: str = PAGE_ID) -> MessengerValidationHttpResponse:
    return MessengerValidationHttpResponse(200, {}, {"id": page_id, "name": "HIRI"})


def conversations_response() -> MessengerValidationHttpResponse:
    return MessengerValidationHttpResponse(
        200,
        {},
        {"data": [{"id": "private-conversation-id"}]},
    )


def successful_transport() -> RecordingTransport:
    return RecordingTransport([identity_response(), conversations_response()])


def validate(client, slug: str, account_id: str, *, token: str | None = None):
    return client.post(
        f"/api/integrations/accounts/{account_id}/validate-connection",
        headers=workspace_headers(slug, token),
    )


def persisted_counts() -> tuple[int, ...]:
    with next(app.dependency_overrides[get_session]()) as session:
        return (
            len(session.exec(select(WorkItem)).all()),
            len(session.exec(select(OutboundIntegrationAction)).all()),
            len(session.exec(select(Lead)).all()),
            len(session.exec(select(ConversationMessage)).all()),
            len(session.exec(select(AIEmployeeCapabilityToolAccess)).all()),
        )


def test_messenger_success_is_exact_read_only_connected_and_inactive(client, monkeypatch):
    slug = "messenger-validator-success"
    create_workspace(client, slug)
    account = provision_account(client, slug)
    for purpose in ("api_access_token", "webhook_app_secret", "webhook_verify_token"):
        attach_reference(client, slug, account["id"], purpose)
    transport = successful_transport()
    install_validator(monkeypatch, transport)
    before = persisted_counts()

    response = validate(client, slug, account["id"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["succeeded"] is True
    assert payload["provider_account_identity"] == PAGE_ID
    assert payload["account"]["connection_status"] == "connected"
    assert payload["account"]["active"] is False
    assert payload["account"]["last_validated_at"] is not None
    assert "provider_identity_matches" in payload["checks_passed"]
    assert "messenger_conversations_access" in payload["checks_passed"]
    assert "local_webhook_app_secret_configured" in payload["checks_passed"]
    assert "local_webhook_verify_token_configured" in payload["checks_passed"]
    assert set(payload["checks_unavailable"]) == {
        "provider_webhook_subscription",
        "provider_webhook_subscription_fields",
        "messenger_advanced_access",
    }
    assert persisted_counts() == before

    assert len(transport.calls) == 2
    assert transport.calls[0][0] == "https://graph.facebook.com/v23.0/me?fields=id,name"
    assert transport.calls[1][0] == (
        "https://graph.facebook.com/v23.0/1302062409649643/conversations"
    )
    for url, headers, timeout in transport.calls:
        assert headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert ACCESS_TOKEN not in url
        assert timeout.connect == 5
        assert timeout.read == 15
    serialized = str(payload)
    assert ACCESS_TOKEN not in serialized
    assert TOKEN_REFERENCE not in serialized
    assert "private-conversation-id" not in serialized

    events = client.get(
        f"/api/integrations/accounts/{account['id']}/audit-events",
        headers=workspace_headers(slug),
    ).json()
    assert {event["action"] for event in events} >= {
        "validation_succeeded",
        "connected",
    }
    assert all(event["actor_user_id"] is not None for event in events)
    assert ACCESS_TOKEN not in str(events)
    assert TOKEN_REFERENCE not in str(events)

    health = client.get(
        f"/api/integrations/accounts/{account['id']}/health",
        headers=workspace_headers(slug),
    ).json()
    assert health["connection_status"] == "connected"
    assert health["health"] == "inactive"


def test_local_webhook_readiness_is_nonblocking(client, monkeypatch):
    slug = "messenger-validator-webhook"
    create_workspace(client, slug)
    account = provision_account(client, slug)
    attach_reference(client, slug, account["id"], "api_access_token")
    install_validator(monkeypatch, successful_transport())

    payload = validate(client, slug, account["id"]).json()

    assert payload["succeeded"] is True
    assert set(payload["checks_failed"]) == {
        "local_webhook_app_secret_configured",
        "local_webhook_verify_token_configured",
    }
    assert "provider_webhook_subscription" in payload["checks_unavailable"]
    assert payload["account"]["connection_status"] == "connected"


@pytest.mark.parametrize(
    ("outcomes", "reason", "temporary"),
    [
        ([identity_response("other-page")], "messenger_page_id_mismatch", False),
        (
            [MessengerValidationHttpResponse(401, {}, {"error": {"code": 190}})],
            "messenger_authentication_failed",
            False,
        ),
        (
            [MessengerValidationHttpResponse(400, {}, {"error": {"code": 190}})],
            "messenger_authentication_failed",
            False,
        ),
        (
            [MessengerValidationHttpResponse(403, {}, {"error": {"message": "raw"}})],
            "messenger_page_access_denied",
            False,
        ),
        (
            [identity_response(), MessengerValidationHttpResponse(403, {}, None)],
            "messenger_messaging_permission_denied",
            False,
        ),
        ([MessengerValidationHttpResponse(429, {}, None)], "messenger_rate_limited", True),
        (
            [MessengerValidationHttpResponse(503, {}, None)],
            "messenger_provider_unavailable",
            True,
        ),
        (
            [
                httpx.ConnectError(
                    "raw network detail",
                    request=httpx.Request("GET", "https://graph.facebook.com"),
                )
            ],
            "messenger_network_error",
            True,
        ),
    ],
)
def test_initial_failures_are_safe_and_remain_configured(
    client,
    monkeypatch,
    outcomes,
    reason,
    temporary,
):
    slug = f"messenger-{reason[-22:].replace('_', '-')}"
    create_workspace(client, slug)
    account = provision_account(client, slug)
    attach_reference(client, slug, account["id"], "api_access_token")
    install_validator(monkeypatch, RecordingTransport(outcomes))

    payload = validate(client, slug, account["id"]).json()

    assert payload["succeeded"] is False
    assert payload["reason_code"] == reason
    assert payload["temporary_failure"] is temporary
    assert payload["account"]["connection_status"] == "configured"
    assert ACCESS_TOKEN not in str(payload)
    assert TOKEN_REFERENCE not in str(payload)
    assert "raw" not in str(payload)

    events = client.get(
        f"/api/integrations/accounts/{account['id']}/audit-events",
        headers=workspace_headers(slug),
    ).json()
    failed = next(event for event in events if event["action"] == "validation_failed")
    assert failed["reason_code"] == reason
    assert ACCESS_TOKEN not in str(events)
    assert "raw" not in str(events)


def test_missing_unresolved_and_expired_token_never_call_meta(client, monkeypatch):
    slug = "messenger-validator-credential"
    create_workspace(client, slug)
    account = provision_account(client, slug)
    transport = successful_transport()
    install_validator(monkeypatch, transport)

    assert validate(client, slug, account["id"]).json()["reason_code"] == (
        "messenger_page_access_token_reference_missing"
    )
    attach_reference(client, slug, account["id"], "api_access_token")
    monkeypatch.delenv(TOKEN_REFERENCE)
    assert validate(client, slug, account["id"]).json()["reason_code"] == (
        "messenger_page_access_token_unavailable"
    )

    expired_slug = "messenger-validator-expired"
    create_workspace(client, expired_slug)
    expired = provision_account(client, expired_slug)
    attach_reference(
        client,
        expired_slug,
        expired["id"],
        "api_access_token",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    monkeypatch.setenv(TOKEN_REFERENCE, ACCESS_TOKEN)
    assert validate(client, expired_slug, expired["id"]).json()["reason_code"] == (
        "messenger_page_access_token_expired"
    )
    assert transport.calls == []


def test_established_auth_and_permission_failures_reconnect_but_transient_preserves(
    client,
    monkeypatch,
):
    slug = "messenger-validator-reconnect"
    create_workspace(client, slug)
    account = provision_account(client, slug)
    attach_reference(client, slug, account["id"], "api_access_token")
    transport = RecordingTransport(
        [
            identity_response(),
            conversations_response(),
            MessengerValidationHttpResponse(503, {}, None),
            identity_response(),
            MessengerValidationHttpResponse(403, {}, None),
        ]
    )
    install_validator(monkeypatch, transport)
    assert validate(client, slug, account["id"]).json()["succeeded"] is True

    transient = validate(client, slug, account["id"]).json()
    assert transient["temporary_failure"] is True
    assert transient["account"]["connection_status"] == "connected"

    denied = validate(client, slug, account["id"]).json()
    assert denied["reason_code"] == "messenger_messaging_permission_denied"
    assert denied["account"]["connection_status"] == "reconnect_required"
    assert denied["account"]["active"] is False


def test_disconnected_cross_workspace_and_member_fail_closed(client, monkeypatch):
    workspace_id = create_workspace(client, "messenger-scope-a")
    create_workspace(client, "messenger-scope-b")
    account = provision_account(client, "messenger-scope-a")
    attach_reference(client, "messenger-scope-a", account["id"], "api_access_token")
    transport = successful_transport()
    install_validator(monkeypatch, transport)

    assert validate(client, "messenger-scope-b", account["id"]).status_code == 404
    with next(app.dependency_overrides[get_session]()) as session:
        settings = app.dependency_overrides[get_settings]()
        auth = AuthenticationService(session, settings)
        user = auth.register(email="messenger-member@example.com", password="password-12345")
        session.add(
            WorkspaceMember(
                workspace_id=workspace_id,
                user_id=user.id,
                role=WorkspaceMemberRole.MEMBER,
            )
        )
        session.commit()
        member_token = auth.issue_access_token(user)
    assert validate(
        client,
        "messenger-scope-a",
        account["id"],
        token=member_token,
    ).status_code == 403
    assert transport.calls == []

    assert validate(client, "messenger-scope-a", account["id"]).status_code == 200
    disconnected = client.post(
        f"/api/integrations/accounts/{account['id']}/disconnect",
        headers=workspace_headers("messenger-scope-a"),
    )
    assert disconnected.status_code == 200
    assert validate(client, "messenger-scope-a", account["id"]).status_code == 409


def test_validator_rejects_provider_host_and_version_without_http(client):
    create_workspace(client, "messenger-validator-direct")
    messenger = provision_account(client, "messenger-validator-direct")
    with next(app.dependency_overrides[get_session]()) as session:
        account = session.get(IntegrationAccount, UUID(messenger["id"]))
        assert account is not None
        transport = successful_transport()
        credential_service = IntegrationCredentialReferenceService(session)
        hostile = FacebookMessengerConnectionValidator(
            credential_service,
            graph_api_base_url="https://attacker.example",
            graph_api_version="v23.0",
            transport=transport,
        )
        bad_version = FacebookMessengerConnectionValidator(
            credential_service,
            graph_api_base_url="https://graph.facebook.com",
            graph_api_version="v999.0",
            transport=transport,
        )
        assert hostile.validate(account).reason_code == (
            "messenger_validator_configuration_invalid"
        )
        assert bad_version.validate(account).reason_code == (
            "messenger_validator_configuration_invalid"
        )
        account.provider = "whatsapp_cloud"
        assert hostile.validate(account).reason_code == "messenger_provider_mismatch"
        assert transport.calls == []


def test_requirements_registry_and_audit_actor(client, monkeypatch):
    requirements = get_provider_requirements("facebook_messenger", None)
    assert requirements is not None
    assert requirements.validation_credential_purposes == {"api_access_token"}
    with next(app.dependency_overrides[get_session]()) as session:
        configured = default_channel_connection_validator_registry(
            session,
            app.dependency_overrides[get_settings](),
        )
        assert isinstance(
            configured.get("facebook_messenger"),
            FacebookMessengerConnectionValidator,
        )

    slug = "messenger-validator-actor"
    create_workspace(client, slug)
    account = provision_account(client, slug)
    attach_reference(client, slug, account["id"], "api_access_token")
    install_validator(monkeypatch, successful_transport())
    assert validate(client, slug, account["id"]).status_code == 200

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
