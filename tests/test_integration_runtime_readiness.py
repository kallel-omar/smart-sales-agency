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


def _headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def _provision(client, slug: str, provider: str = "generic_hmac") -> dict:
    assert client.post(
        "/api/workspaces", json={"slug": slug, "name": slug}
    ).status_code == 201
    response = client.post(
        "/api/integrations/accounts",
        headers=_headers(slug),
        json={
            "provider": provider,
            "external_account_id": slug,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
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
        "status": "ready",
        "configuration_ready": True,
        "external_provider_availability_checked": False,
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


def test_runtime_readiness_reports_unregistered_adapter_and_generic_webhook_configuration(
    client, monkeypatch
):
    missing_adapter = _provision(client, "missing-adapter", provider="unsupported-provider")
    missing_adapter_response = _readiness(client, "missing-adapter", missing_adapter["id"])
    assert _codes(missing_adapter_response) == {
        "inbound_verifier_not_configured",
        "outbound_adapter_not_registered",
    }

    missing_configuration = _provision(client, "webhook-missing", provider="generic_webhook")
    missing_response = _readiness(client, "webhook-missing", missing_configuration["id"])
    assert "outbound_configuration_missing" in _codes(missing_response)

    monkeypatch.setitem(
        app.dependency_overrides,
        get_settings,
        lambda: Settings(
            environment="test",
            database_url="sqlite://",
            llm_mode="demo",
            outbound_webhook_url="not a URL",
        ),
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
