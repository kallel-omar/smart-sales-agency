from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import select

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import IntegrationAccount, IntegrationAccountAuditEvent, Workspace
from app.services.integration_account_audit import (
    IntegrationAccountAuditRetentionPolicy,
    IntegrationAccountAuditService,
)


def create_workspace(client, slug: str) -> None:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201


def workspace_headers(slug: str) -> dict[str, str]:
    return {"X-Workspace-Slug": slug}


def provision_account(client, workspace_slug: str, external_account_id: str) -> dict:
    response = client.post(
        "/api/integrations/accounts",
        headers=workspace_headers(workspace_slug),
        json={
            "provider": "generic_hmac",
            "external_account_id": external_account_id,
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    assert response.status_code == 201
    return response.json()


def event_ids_for_account(account_id: str) -> list[UUID]:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        return list(
            session.exec(
                select(IntegrationAccountAuditEvent.id).where(
                    IntegrationAccountAuditEvent.integration_account_id == UUID(account_id)
                )
            ).all()
        )


def set_event_time(event_id: UUID, created_at: datetime) -> None:
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        event = session.get(IntegrationAccountAuditEvent, event_id)
        assert event is not None
        event.created_at = created_at
        session.add(event)
        session.commit()


def test_explicit_workspace_cleanup_deletes_only_expired_events_and_is_idempotent(client):
    create_workspace(client, "company-a")
    create_workspace(client, "company-b")
    expired_account = provision_account(client, "company-a", "expired")
    recent_account = provision_account(client, "company-a", "recent")
    other_workspace_account = provision_account(client, "company-b", "other-workspace")
    expired_event_id = event_ids_for_account(expired_account["id"])[0]
    recent_event_id = event_ids_for_account(recent_account["id"])[0]
    other_workspace_event_id = event_ids_for_account(other_workspace_account["id"])[0]

    now = datetime.now(UTC)
    set_event_time(expired_event_id, now - timedelta(days=91))
    set_event_time(recent_event_id, now - timedelta(days=89))
    set_event_time(other_workspace_event_id, now - timedelta(days=91))

    response = client.post(
        "/api/integrations/audit-events/retention-cleanup",
        headers=workspace_headers("company-a"),
    )
    assert response.status_code == 200
    assert set(response.json()) == {"deleted_count", "cutoff"}
    assert response.json()["deleted_count"] == 1

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        assert session.get(IntegrationAccount, UUID(expired_account["id"])) is not None
        assert session.get(IntegrationAccount, UUID(recent_account["id"])) is not None
        assert session.get(IntegrationAccountAuditEvent, expired_event_id) is None
        assert session.get(IntegrationAccountAuditEvent, recent_event_id) is not None
        assert session.get(IntegrationAccountAuditEvent, other_workspace_event_id) is not None

    repeated = client.post(
        "/api/integrations/audit-events/retention-cleanup",
        headers=workspace_headers("company-a"),
    )
    assert repeated.status_code == 200
    assert repeated.json()["deleted_count"] == 0


def test_cleanup_retains_records_exactly_at_cutoff_and_processes_multiple_batches(client):
    create_workspace(client, "company-a")
    account = provision_account(client, "company-a", "batched")
    account_id = UUID(account["id"])
    session_dependency = app.dependency_overrides[get_session]
    now = datetime(2026, 1, 1, tzinfo=UTC)
    policy = IntegrationAccountAuditRetentionPolicy(retention_days=90)
    cutoff = policy.cutoff(now)

    with next(session_dependency()) as session:
        workspace = session.get(Workspace, UUID(account["workspace_id"]))
        assert workspace is not None
        original_event = session.exec(
            select(IntegrationAccountAuditEvent).where(
                IntegrationAccountAuditEvent.integration_account_id == account_id
            )
        ).one()
        original_event.created_at = cutoff
        session.add(original_event)
        for _ in range(3):
            session.add(
                IntegrationAccountAuditEvent(
                    workspace_id=workspace.id,
                    integration_account_id=account_id,
                    action=original_event.action,
                    created_at=cutoff - timedelta(microseconds=1),
                )
            )
        session.commit()

        result = IntegrationAccountAuditService(session).cleanup_for_workspace(
            workspace,
            policy,
            now=now,
            batch_size=2,
        )

        assert result.deleted_count == 3
        remaining = list(
            session.exec(
                select(IntegrationAccountAuditEvent).where(
                    IntegrationAccountAuditEvent.integration_account_id == account_id
                )
            ).all()
        )
        assert len(remaining) == 1
        assert remaining[0].created_at.replace(tzinfo=UTC) == cutoff
        persisted_account = session.get(IntegrationAccount, account_id)
        assert persisted_account is not None
        assert persisted_account.active is True


def test_cleanup_honors_configured_retention_days(client):
    create_workspace(client, "company-a")
    old_account = provision_account(client, "company-a", "old")
    retained_account = provision_account(client, "company-a", "retained")
    now = datetime.now(UTC)
    set_event_time(event_ids_for_account(old_account["id"])[0], now - timedelta(days=2))
    set_event_time(event_ids_for_account(retained_account["id"])[0], now - timedelta(hours=12))

    app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="demo",
        require_human_approval=True,
        integration_account_audit_retention_days=1,
    )
    response = client.post(
        "/api/integrations/audit-events/retention-cleanup",
        headers=workspace_headers("company-a"),
    )
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 1
    assert event_ids_for_account(old_account["id"]) == []
    assert len(event_ids_for_account(retained_account["id"])) == 1
