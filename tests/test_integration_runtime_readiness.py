from uuid import UUID

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import (
    IntegrationAccount,
    OutboundIntegrationActionType,
    Workspace,
)
from app.services.delivery_adapters import (
    DeliveryAdapterCapabilities,
    DeliveryAdapterRegistry,
    HttpxWebhookHttpTransport,
)
from app.services.integration_runtime_readiness import IntegrationRuntimeReadinessService


TEST_AUTH_TOKEN_SECRET = "test-auth-token-secret-32-byte-value"
WHATSAPP_ACCOUNT_IDENTIFIER = "test-whatsapp-phone-number-id"


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _provision(
    client,
    slug: str,
    provider: str = "generic_hmac",
    *,
    external_account_id: str | None = None,
    secret_reference: str = "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
) -> dict:
    if external_account_id is None:
        external_account_id = slug
    assert client.post(
        "/api/workspaces", json={"slug": slug, "name": slug}
    ).status_code == 201
    response = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={
            "provider": provider,
            "external_account_id": external_account_id,
            "secret_reference": secret_reference,
        },
    )
    assert response.status_code == 201
    return response.json()


def _readiness(client, slug: str, account_id: str):
    return client.get(
        f"/api/integrations/accounts/{account_id}/health/runtime-readiness",
        headers=_headers(slug),
    )


def _update_account(client, account_id: str, **values) -> None:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        account = session.get(IntegrationAccount, UUID(account_id))
        assert account is not None
        for field, value in values.items():
            setattr(account, field, value)
        session.add(account)
        session.commit()


def _update_workspace(client, slug: str, **values) -> None:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.query(Workspace).filter(Workspace.slug == slug).one()
        for field, value in values.items():
            setattr(workspace, field, value)
        session.add(workspace)
        session.commit()


def _codes(response) -> set[str]:
    return set(response.json()["blocking_reasons"])


def _capability(data: dict, capability: str) -> dict:
    return next(
        item for item in data["capability_readiness"] if item["capability"] == capability
    )


def _settings(**overrides) -> Settings:
    values = {
        "environment": "test",
        "database_url": "sqlite://",
        "llm_mode": "demo",
        "auth_token_secret": TEST_AUTH_TOKEN_SECRET,
        "require_human_approval": True,
    }
    values.update(overrides)
    return Settings(**values)


def _configure_readiness_settings(monkeypatch, **overrides) -> None:
    monkeypatch.setitem(
        app.dependency_overrides,
        get_settings,
        lambda: _settings(**overrides),
    )


def _configure_whatsapp_runtime_ready(monkeypatch, **overrides) -> None:
    values = {
        "outbound_webhook_url": "https://n8n.test/webhook/whatsapp-cloud",
        "outbound_webhook_signing_enabled": True,
    }
    values.update(overrides)
    _configure_readiness_settings(monkeypatch, **values)


def test_runtime_readiness_reports_a_fully_configured_mvp_account_without_provider_probe(
    client, monkeypatch
):
    account = _provision(client, "ready")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("runtime readiness must not execute outbound HTTP")

    monkeypatch.setattr(HttpxWebhookHttpTransport, "post", fail_if_called)
    response = _readiness(client, "ready", account["id"])

    assert response.status_code == 200
    assert response.json() == {
        "id": account["id"],
        "provider": "generic_hmac",
        "active": True,
        "status": "ready",
        "configuration_ready": True,
        "external_provider_availability_checked": False,
        "supported_capabilities": ["inbound_messages", "outbound_messages"],
        "capability_readiness": [
            {
                "capability": "inbound_messages",
                "supported": True,
                "ready": True,
                "blocking_reasons": [],
                "blocking_reason_details": [],
            },
            {
                "capability": "outbound_messages",
                "supported": True,
                "ready": True,
                "blocking_reasons": [],
                "blocking_reason_details": [],
            },
            {
                "capability": "outbound_approval_gate",
                "supported": False,
                "ready": False,
                "blocking_reasons": ["provider_capability_not_supported"],
                "blocking_reason_details": [
                    {
                        "code": "provider_capability_not_supported",
                        "message": (
                            "This provider does not support the requested channel capability."
                        ),
                    }
                ],
            },
            {
                "capability": "provider_delivery_status",
                "supported": False,
                "ready": False,
                "blocking_reasons": ["provider_capability_not_supported"],
                "blocking_reason_details": [
                    {
                        "code": "provider_capability_not_supported",
                        "message": (
                            "This provider does not support the requested channel capability."
                        ),
                    }
                ],
            },
        ],
        "blocking_reasons": [],
        "blocking_reason_details": [],
    }


def test_runtime_readiness_reports_inactive_workspace_and_account(client):
    workspace_account = _provision(client, "workspace-inactive")
    _update_workspace(client, "workspace-inactive", active=False)
    workspace_response = _readiness(client, "workspace-inactive", workspace_account["id"])
    assert workspace_response.status_code == 200
    assert workspace_response.json()["status"] == "blocked"
    assert _codes(workspace_response) == {"workspace_inactive"}

    account = _provision(client, "account-inactive")
    _update_account(client, account["id"], active=False)
    account_response = _readiness(client, "account-inactive", account["id"])
    assert account_response.status_code == 200
    assert account_response.json()["active"] is False
    assert _codes(account_response) == {"integration_account_inactive"}


def test_runtime_readiness_validates_required_secret_references_without_exposure(client):
    missing = _provision(client, "secret-missing")
    _update_account(client, missing["id"], secret_reference=None)
    missing_response = _readiness(client, "secret-missing", missing["id"])
    assert _codes(missing_response) == {"secret_reference_missing"}

    invalid = _provision(client, "secret-invalid")
    _update_account(client, invalid["id"], secret_reference="DATABASE_URL")
    invalid_response = _readiness(client, "secret-invalid", invalid["id"])
    assert _codes(invalid_response) == {"secret_reference_invalid"}

    unresolved = _provision(client, "secret-unresolved")
    _update_account(
        client,
        unresolved["id"],
        secret_reference="INTEGRATION_SECRET_UNRESOLVED_RUNTIME_READINESS",
    )
    unresolved_response = _readiness(client, "secret-unresolved", unresolved["id"])
    assert _codes(unresolved_response) == {"secret_unresolvable"}
    serialized = str(unresolved_response.json())
    for sensitive in (
        "secret_reference",
        "DATABASE_URL",
        "INTEGRATION_SECRET_UNRESOLVED_RUNTIME_READINESS",
        "test-generic-hmac-secret",
    ):
        assert sensitive not in serialized


def test_whatsapp_cloud_channel_readiness_reports_supported_capabilities(
    client,
    monkeypatch,
):
    _configure_whatsapp_runtime_ready(monkeypatch)
    account = _provision(
        client,
        "whatsapp-ready",
        provider="whatsapp_cloud",
        external_account_id=WHATSAPP_ACCOUNT_IDENTIFIER,
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("runtime readiness must not contact providers")

    monkeypatch.setattr(HttpxWebhookHttpTransport, "post", fail_if_called)
    response = _readiness(client, "whatsapp-ready", account["id"])

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == account["id"]
    assert data["provider"] == "whatsapp_cloud"
    assert data["active"] is True
    assert data["status"] == "ready"
    assert data["configuration_ready"] is True
    assert data["external_provider_availability_checked"] is False
    assert data["supported_capabilities"] == [
        "inbound_messages",
        "outbound_messages",
        "outbound_approval_gate",
        "provider_delivery_status",
    ]
    assert data["blocking_reasons"] == []
    assert all(item["supported"] is True for item in data["capability_readiness"])
    assert all(item["ready"] is True for item in data["capability_readiness"])


def test_whatsapp_readiness_blocks_missing_provider_account_identifier(
    client,
    monkeypatch,
):
    _configure_whatsapp_runtime_ready(monkeypatch)
    account = _provision(
        client,
        "whatsapp-missing-id",
        provider="whatsapp_cloud",
        external_account_id=WHATSAPP_ACCOUNT_IDENTIFIER,
    )
    _update_account(client, account["id"], external_account_id=None)

    response = _readiness(client, "whatsapp-missing-id", account["id"])

    assert response.status_code == 200
    data = response.json()
    assert data["configuration_ready"] is False
    assert "external_account_id_missing" in data["blocking_reasons"]
    assert "external_account_id_missing" in _capability(
        data, "inbound_messages"
    )["blocking_reasons"]
    assert "external_account_id_missing" in _capability(
        data, "outbound_messages"
    )["blocking_reasons"]


def test_whatsapp_readiness_blocks_missing_unresolvable_secret_reference_safely(
    client,
    monkeypatch,
):
    _configure_whatsapp_runtime_ready(monkeypatch)
    missing = _provision(
        client,
        "whatsapp-secret-missing",
        provider="whatsapp_cloud",
        external_account_id=WHATSAPP_ACCOUNT_IDENTIFIER,
    )
    _update_account(client, missing["id"], secret_reference=None)
    missing_response = _readiness(client, "whatsapp-secret-missing", missing["id"])
    assert "secret_reference_missing" in _codes(missing_response)

    unresolved = _provision(
        client,
        "whatsapp-secret-unresolved",
        provider="whatsapp_cloud",
        external_account_id=WHATSAPP_ACCOUNT_IDENTIFIER,
    )
    _update_account(
        client,
        unresolved["id"],
        secret_reference="INTEGRATION_SECRET_UNRESOLVED_WHATSAPP_READINESS",
    )
    unresolved_response = _readiness(
        client,
        "whatsapp-secret-unresolved",
        unresolved["id"],
    )
    assert "secret_unresolvable" in _codes(unresolved_response)
    serialized = str(unresolved_response.json())
    for sensitive in (
        "INTEGRATION_SECRET_UNRESOLVED_WHATSAPP_READINESS",
        "test-generic-hmac-secret",
    ):
        assert sensitive not in serialized


def test_whatsapp_readiness_blocks_missing_outbound_webhook_configuration(client, monkeypatch):
    _configure_whatsapp_runtime_ready(monkeypatch, outbound_webhook_url="")
    account = _provision(
        client,
        "whatsapp-webhook-missing",
        provider="whatsapp_cloud",
        external_account_id=WHATSAPP_ACCOUNT_IDENTIFIER,
    )

    response = _readiness(client, "whatsapp-webhook-missing", account["id"])

    data = response.json()
    assert response.status_code == 200
    assert "outbound_configuration_missing" in data["blocking_reasons"]
    assert _capability(data, "inbound_messages")["ready"] is True
    assert "outbound_configuration_missing" in _capability(
        data, "outbound_messages"
    )["blocking_reasons"]


def test_whatsapp_readiness_blocks_unsigned_outbound_transport(client, monkeypatch):
    _configure_whatsapp_runtime_ready(
        monkeypatch,
        outbound_webhook_signing_enabled=False,
    )
    account = _provision(
        client,
        "whatsapp-signing-disabled",
        provider="whatsapp_cloud",
        external_account_id=WHATSAPP_ACCOUNT_IDENTIFIER,
    )

    response = _readiness(client, "whatsapp-signing-disabled", account["id"])

    data = response.json()
    assert response.status_code == 200
    assert "outbound_webhook_signing_disabled" in data["blocking_reasons"]
    assert "outbound_webhook_signing_disabled" in _capability(
        data, "outbound_messages"
    )["blocking_reasons"]
    assert "outbound_webhook_signing_disabled" in _capability(
        data, "outbound_approval_gate"
    )["blocking_reasons"]
    assert _capability(data, "provider_delivery_status")["ready"] is True


def test_whatsapp_readiness_blocks_disabled_approval_gate(client, monkeypatch):
    _configure_whatsapp_runtime_ready(monkeypatch, require_human_approval=False)
    account = _provision(
        client,
        "whatsapp-approval-disabled",
        provider="whatsapp_cloud",
        external_account_id=WHATSAPP_ACCOUNT_IDENTIFIER,
    )

    response = _readiness(client, "whatsapp-approval-disabled", account["id"])

    data = response.json()
    assert response.status_code == 200
    assert "outbound_approval_gate_disabled" in data["blocking_reasons"]
    assert _capability(data, "outbound_messages")["ready"] is True
    assert "outbound_approval_gate_disabled" in _capability(
        data, "outbound_approval_gate"
    )["blocking_reasons"]


def test_unsupported_provider_does_not_inherit_whatsapp_capabilities(client):
    account = _provision(client, "unsupported-channel", provider="unsupported-provider")

    response = _readiness(client, "unsupported-channel", account["id"])

    data = response.json()
    assert response.status_code == 200
    assert data["configuration_ready"] is False
    assert data["supported_capabilities"] == []
    assert all(item["supported"] is False for item in data["capability_readiness"])
    assert all(
        item["blocking_reasons"] == ["provider_capability_not_supported"]
        for item in data["capability_readiness"]
    )
    assert _codes(response) == {
        "inbound_verifier_not_configured",
        "outbound_adapter_not_registered",
    }


def test_whatsapp_readiness_response_excludes_secrets_tokens_targets_and_provider_ids(
    client,
    monkeypatch,
):
    monkeypatch.setenv("WHATSAPP_CLOUD_ACCESS_TOKEN", "test-meta-access-token")
    _configure_whatsapp_runtime_ready(monkeypatch)
    account = _provision(
        client,
        "whatsapp-secret-hygiene",
        provider="whatsapp_cloud",
        external_account_id=WHATSAPP_ACCOUNT_IDENTIFIER,
    )

    response = _readiness(client, "whatsapp-secret-hygiene", account["id"])

    serialized = str(response.json())
    for forbidden in (
        "test-generic-hmac-secret",
        "test-meta-access-token",
        "Bearer",
        WHATSAPP_ACCOUNT_IDENTIFIER,
        "secret_reference",
        "credential",
    ):
        assert forbidden not in serialized


def test_runtime_readiness_rejects_query_workspace_bypass(client):
    account = _provision(client, "query-company-a")
    assert client.post(
        "/api/workspaces", json={"slug": "query-company-b", "name": "query-company-b"}
    ).status_code == 201

    response = client.get(
        (
            f"/api/integrations/accounts/{account['id']}/health/runtime-readiness"
            "?workspace_slug=query-company-a"
        ),
        headers=_headers("query-company-b"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Integration account not found"


def test_runtime_readiness_reports_unregistered_adapter_and_generic_webhook_configuration(
    client, monkeypatch
):
    missing_adapter = _provision(client, "missing-adapter", provider="unsupported-provider")
    missing_adapter_response = _readiness(client, "missing-adapter", missing_adapter["id"])
    assert _codes(missing_adapter_response) == {
        "inbound_verifier_not_configured",
        "outbound_adapter_not_registered",
    }

    _configure_readiness_settings(monkeypatch, outbound_webhook_url="")
    missing_configuration = _provision(client, "webhook-missing", provider="generic_webhook")
    missing_response = _readiness(client, "webhook-missing", missing_configuration["id"])
    assert "outbound_configuration_missing" in _codes(missing_response)

    monkeypatch.setitem(
        app.dependency_overrides,
        get_settings,
        lambda: _settings(outbound_webhook_url="not a URL"),
    )
    invalid_configuration = _provision(client, "webhook-invalid", provider="generic_webhook")
    invalid_response = _readiness(client, "webhook-invalid", invalid_configuration["id"])
    assert "outbound_configuration_invalid" in _codes(invalid_response)


def test_runtime_readiness_checks_adapter_capabilities_without_delivery(client):
    account = _provision(client, "capability")
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        workspace = session.query(Workspace).filter(Workspace.slug == "capability").one()
        registry = DeliveryAdapterRegistry(
            {"generic_hmac": _MediaOnlyAdapter()}
        )
        result = IntegrationRuntimeReadinessService(
            session,
            Settings(environment="test", database_url="sqlite://", llm_mode="demo"),
            registry,
        ).evaluate(workspace, UUID(account["id"]))

    assert result.configuration_ready is False
    assert [str(blocker.code) for blocker in result.blockers] == [
        "outbound_adapter_capability_mismatch"
    ]


def test_runtime_readiness_is_workspace_scoped(client):
    account = _provision(client, "company-a")
    assert client.post(
        "/api/workspaces", json={"slug": "company-b", "name": "company-b"}
    ).status_code == 201

    response = _readiness(client, "company-b", account["id"])
    assert response.status_code == 404
    assert response.json()["detail"] == "Integration account not found"


class _MediaOnlyAdapter:
    capabilities = DeliveryAdapterCapabilities(
        supported_action_types=frozenset({OutboundIntegrationActionType.SEND_MEDIA})
    )

    def deliver(self, action, account):
        raise AssertionError("runtime readiness must not invoke delivery")
