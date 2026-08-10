from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    Lead,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.services.approval_decisions import (
    ApprovalDecisionActor,
    ApprovalDecisionService,
)
from app.services.authentication import AuthenticationService


TEST_PASSWORD = "correct-password"


def _settings():
    return app.dependency_overrides[get_settings]()


def _session():
    return next(app.dependency_overrides[get_session]())


def _new_user(email: str) -> tuple[UUID, str]:
    with _session() as session:
        service = AuthenticationService(session, _settings())
        user = service.register(email=email, password=TEST_PASSWORD)
        token = service.issue_access_token(user)
        return user.id, token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workspace_headers(slug: str, token: str) -> dict[str, str]:
    return {**_auth_headers(token), "X-Workspace-Slug": slug}


def _create_workspace(client, slug: str, token: str) -> UUID:
    response = client.post(
        "/api/workspaces",
        headers=_auth_headers(token),
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def _create_lead(client, slug: str, token: str) -> UUID:
    response = client.post(
        "/api/leads",
        headers=_workspace_headers(slug, token),
        json={
            "tenant_id": "body-is-not-authority",
            "full_name": f"Lead {slug}",
            "company_name": f"{slug} Customer",
            "source": "manual",
        },
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def _add_membership(
    workspace_id: UUID,
    user_id: UUID,
    role: WorkspaceMemberRole,
    *,
    active: bool = True,
) -> UUID:
    with _session() as session:
        membership = WorkspaceMember(
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            active=active,
        )
        session.add(membership)
        session.commit()
        return membership.id


def _membership(workspace_id: UUID, user_id: UUID) -> WorkspaceMember:
    with _session() as session:
        membership = session.exec(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        ).one()
        session.expunge(membership)
        return membership


def _set_membership_role(membership_id: UUID, role: WorkspaceMemberRole) -> None:
    with _session() as session:
        membership = session.get(WorkspaceMember, membership_id)
        assert membership is not None
        membership.role = role
        session.add(membership)
        session.commit()


def _set_membership_active(membership_id: UUID, active: bool) -> None:
    with _session() as session:
        membership = session.get(WorkspaceMember, membership_id)
        assert membership is not None
        membership.active = active
        session.add(membership)
        session.commit()


def _deactivate_user(user_id: UUID) -> None:
    with _session() as session:
        user = session.get(User, user_id)
        assert user is not None
        user.active = False
        session.add(user)
        session.commit()


def _create_approval(lead_id: UUID, *, status: ApprovalStatus = ApprovalStatus.PENDING) -> UUID:
    with _session() as session:
        approval = ApprovalRequest(
            lead_id=lead_id,
            channel="console",
            payload={"recipient": "customer", "content": "Approved message"},
            status=status,
        )
        session.add(approval)
        session.commit()
        return approval.id


def _approval(approval_id: UUID) -> ApprovalRequest:
    with _session() as session:
        approval = session.get(ApprovalRequest, approval_id)
        assert approval is not None
        session.expunge(approval)
        return approval


def _message_count() -> int:
    with _session() as session:
        return len(session.exec(select(ConversationMessage)).all())


def _forged_token(user_id: UUID, **extra_claims) -> str:
    settings = _settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iss": settings.auth_token_issuer,
            **extra_claims,
        },
        settings.auth_token_secret.get_secret_value(),
        algorithm=settings.auth_token_algorithm,
    )


def test_owner_can_approve_and_actor_is_persisted_and_exposed_safely(client):
    owner_id, owner_token = _new_user("approval-owner@example.com")
    workspace_id = _create_workspace(client, "approval-owner", owner_token)
    owner_membership = _membership(workspace_id, owner_id)
    lead_id = _create_lead(client, "approval-owner", owner_token)
    approval_id = _create_approval(lead_id)

    response = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers={
            **_workspace_headers("approval-owner", owner_token),
            "X-Approval-Actor-User-Id": "not-authority",
        },
        json={
            "reviewer_note": "Ship it",
            "decided_by_user_id": "00000000-0000-0000-0000-000000000000",
            "decided_by_membership_id": "00000000-0000-0000-0000-000000000000",
            "decided_by_role": "member",
        },
    )

    body = response.json()
    stored = _approval(approval_id)
    assert response.status_code == 200
    assert body["status"] == ApprovalStatus.EXECUTED.value
    assert body["decided_by_user_id"] == str(owner_id)
    assert body["decided_by_membership_id"] == str(owner_membership.id)
    assert body["decided_by_role"] == WorkspaceMemberRole.OWNER.value
    assert stored.decided_by_user_id == owner_id
    assert stored.decided_by_membership_id == owner_membership.id
    assert stored.decided_by_role is WorkspaceMemberRole.OWNER
    assert stored.decided_at is not None
    assert _message_count() == 1
    assert {"password", "password_hash", "token", "credential"}.isdisjoint(body)


@pytest.mark.parametrize(
    ("role", "path"),
    [
        (WorkspaceMemberRole.ADMIN, "approve"),
        (WorkspaceMemberRole.ADMIN, "reject"),
        (WorkspaceMemberRole.OWNER, "reject"),
    ],
)
def test_owner_and_admin_decisions_persist_actor_snapshot(client, role, path):
    owner_id, owner_token = _new_user(f"approval-{role}-{path}-owner@example.com")
    actor_id, actor_token = _new_user(f"approval-{role}-{path}-actor@example.com")
    workspace_id = _create_workspace(client, f"approval-{role}-{path}", owner_token)
    actor_membership_id = _add_membership(workspace_id, actor_id, role)
    lead_id = _create_lead(client, f"approval-{role}-{path}", owner_token)
    approval_id = _create_approval(lead_id)
    token = owner_token if role is WorkspaceMemberRole.OWNER else actor_token
    expected_user_id = owner_id if role is WorkspaceMemberRole.OWNER else actor_id
    expected_membership_id = _membership(workspace_id, owner_id).id if role is WorkspaceMemberRole.OWNER else actor_membership_id

    response = client.post(
        f"/api/approvals/{approval_id}/{path}",
        headers=_workspace_headers(f"approval-{role}-{path}", token),
        json={"reviewer_note": f"{role.value} {path}"},
    )

    stored = _approval(approval_id)
    assert response.status_code == 200
    assert response.json()["decided_by_user_id"] == str(expected_user_id)
    assert response.json()["decided_by_membership_id"] == str(expected_membership_id)
    assert response.json()["decided_by_role"] == role.value
    assert stored.decided_by_role is role
    assert stored.decided_by_user_id == expected_user_id
    assert stored.decided_by_membership_id == expected_membership_id


def test_member_denial_and_boundary_failures_do_not_persist_actor(client):
    owner_id, owner_token = _new_user("approval-boundary-owner@example.com")
    member_id, member_token = _new_user("approval-boundary-member@example.com")
    outsider_id, outsider_token = _new_user("approval-boundary-outsider@example.com")
    workspace_id = _create_workspace(client, "approval-boundary", owner_token)
    other_workspace_id = _create_workspace(client, "approval-boundary-other", owner_token)
    member_membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    _add_membership(other_workspace_id, outsider_id, WorkspaceMemberRole.ADMIN)
    lead_id = _create_lead(client, "approval-boundary", owner_token)
    approval_id = _create_approval(lead_id)

    with TestClient(app) as public_client:
        missing_bearer = public_client.post(
            f"/api/approvals/{approval_id}/approve",
            headers={"X-Workspace-Slug": "approval-boundary"},
            json={},
        )
    member_denied = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=_workspace_headers("approval-boundary", member_token),
        json={"decided_by_user_id": str(owner_id), "role": "owner"},
    )
    no_membership = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=_workspace_headers("approval-boundary", outsider_token),
        json={},
    )
    cross_workspace = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=_workspace_headers("approval-boundary-other", outsider_token),
        json={},
    )
    _set_membership_active(member_membership_id, False)
    inactive_membership = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=_workspace_headers("approval-boundary", member_token),
        json={},
    )

    stored = _approval(approval_id)
    assert missing_bearer.status_code == 401
    assert member_denied.status_code == 403
    assert no_membership.status_code == 404
    assert cross_workspace.status_code == 404
    assert inactive_membership.status_code == 404
    assert stored.status is ApprovalStatus.PENDING
    assert stored.decided_by_user_id is None
    assert stored.decided_by_membership_id is None
    assert stored.decided_by_role is None
    assert stored.decided_at is None


def test_body_headers_and_jwt_claims_cannot_choose_actor(client):
    owner_id, owner_token = _new_user("approval-spoof-owner@example.com")
    admin_id, admin_token = _new_user("approval-spoof-admin@example.com")
    workspace_id = _create_workspace(client, "approval-spoof", owner_token)
    admin_membership_id = _add_membership(workspace_id, admin_id, WorkspaceMemberRole.ADMIN)
    lead_id = _create_lead(client, "approval-spoof", owner_token)
    approval_id = _create_approval(lead_id)
    forged_admin_token = _forged_token(
        admin_id,
        role="owner",
        workspace_role="owner",
        workspace_id=str(workspace_id),
        decided_by_user_id=str(owner_id),
    )

    response = client.post(
        f"/api/approvals/{approval_id}/reject",
        headers={
            **_workspace_headers("approval-spoof", forged_admin_token),
            "X-Approval-Actor-User-Id": str(owner_id),
            "X-Workspace-Role": "owner",
        },
        json={
            "reviewer_note": "Reject with spoofed actor fields",
            "decided_by_user_id": str(owner_id),
            "decided_by_membership_id": str(_membership(workspace_id, owner_id).id),
            "decided_by_role": "owner",
        },
    )

    stored = _approval(approval_id)
    assert response.status_code == 200
    assert stored.decided_by_user_id == admin_id
    assert stored.decided_by_membership_id == admin_membership_id
    assert stored.decided_by_role is WorkspaceMemberRole.ADMIN


def test_repeated_decision_role_change_and_lifecycle_changes_do_not_rewrite_actor(client):
    owner_id, owner_token = _new_user("approval-immutable-owner@example.com")
    admin_id, admin_token = _new_user("approval-immutable-admin@example.com")
    workspace_id = _create_workspace(client, "approval-immutable", owner_token)
    admin_membership_id = _add_membership(workspace_id, admin_id, WorkspaceMemberRole.ADMIN)
    lead_id = _create_lead(client, "approval-immutable", owner_token)
    approval_id = _create_approval(lead_id)

    first = client.post(
        f"/api/approvals/{approval_id}/reject",
        headers=_workspace_headers("approval-immutable", admin_token),
        json={"reviewer_note": "Original admin decision"},
    )
    _set_membership_role(admin_membership_id, WorkspaceMemberRole.MEMBER)
    repeated = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=_workspace_headers("approval-immutable", owner_token),
        json={"reviewer_note": "Should not overwrite"},
    )
    _deactivate_user(admin_id)
    _set_membership_active(admin_membership_id, False)

    stored = _approval(approval_id)
    assert first.status_code == 200
    assert repeated.status_code == 409
    assert stored.status is ApprovalStatus.REJECTED
    assert stored.reviewer_note == "Original admin decision"
    assert stored.decided_by_user_id == admin_id
    assert stored.decided_by_membership_id == admin_membership_id
    assert stored.decided_by_role is WorkspaceMemberRole.ADMIN


def test_legacy_approval_without_actor_remains_readable(client):
    _, owner_token = _new_user("approval-legacy-owner@example.com")
    _create_workspace(client, "approval-legacy", owner_token)
    lead_id = _create_lead(client, "approval-legacy", owner_token)
    approval_id = _create_approval(lead_id, status=ApprovalStatus.APPROVED)

    response = client.get(
        "/api/approvals",
        headers=_workspace_headers("approval-legacy", owner_token),
    )

    row = next(item for item in response.json() if item["id"] == str(approval_id))
    assert response.status_code == 200
    assert row["status"] == ApprovalStatus.APPROVED.value
    assert row["decided_by_user_id"] is None
    assert row["decided_by_membership_id"] is None
    assert row["decided_by_role"] is None


def test_outbound_approval_attribution_preserves_delivery_flow_and_machine_separation(
    client,
    signed_webhook_request,
):
    _, owner_token = _new_user("approval-outbound-owner@example.com")
    _create_workspace(client, "approval-outbound", owner_token)
    account = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers("approval-outbound", owner_token),
        json={
            "provider": "generic_hmac",
            "external_account_id": "approval-outbound",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    action = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_workspace_headers("approval-outbound", owner_token),
        json={
            "external_target_id": "recipient",
            "action_type": "send_message",
            "content": "Approved outbound content",
            "idempotency_key": "approval-outbound-action",
            "requires_approval": True,
        },
    ).json()
    integration_key = account["inbound_credential"]

    integration_key_as_bearer = client.post(
        f"/api/approvals/{action['approval_request_id']}/approve",
        headers=_workspace_headers("approval-outbound", integration_key),
        json={},
    )
    approved = client.post(
        f"/api/approvals/{action['approval_request_id']}/approve",
        headers=_workspace_headers("approval-outbound", owner_token),
        json={},
    )
    approval_status = client.get(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/approval-status",
        headers=_workspace_headers("approval-outbound", owner_token),
    )
    delivered = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/deliver",
        headers=_workspace_headers("approval-outbound", owner_token),
    )
    headers, body = signed_webhook_request(
        integration_key,
        {
            "lead_id": "00000000-0000-0000-0000-000000000000",
            "channel": "console",
            "content": "Machine auth remains separate",
        },
    )
    with TestClient(app) as machine_client:
        machine_request = machine_client.post(
            "/api/integrations/inbound-events",
            headers=headers,
            content=body,
        )

    assert integration_key_as_bearer.status_code == 401
    assert approved.status_code == 200
    assert approved.json()["status"] == ApprovalStatus.APPROVED.value
    assert approved.json()["decided_by_user_id"] is not None
    assert approval_status.status_code == 200
    assert approval_status.json()["decided_by_user_id"] == approved.json()["decided_by_user_id"]
    assert approval_status.json()["decided_by_membership_id"] == approved.json()["decided_by_membership_id"]
    assert approval_status.json()["decided_by_role"] == WorkspaceMemberRole.OWNER.value
    assert delivered.status_code == 200
    assert machine_request.status_code in {401, 404, 422}


@pytest.mark.asyncio
async def test_decision_and_actor_roll_back_together_when_commit_fails(client, monkeypatch):
    owner_id, owner_token = _new_user("approval-rollback-owner@example.com")
    workspace_id = _create_workspace(client, "approval-rollback", owner_token)
    membership = _membership(workspace_id, owner_id)
    lead_id = _create_lead(client, "approval-rollback", owner_token)
    approval_id = _create_approval(lead_id)
    session = _session()

    try:
        workspace = session.get(Workspace, workspace_id)
        assert workspace is not None
        actor = ApprovalDecisionActor(
            user_id=owner_id,
            membership_id=membership.id,
            workspace_id=workspace_id,
            role=WorkspaceMemberRole.OWNER,
        )

        def fail_commit():
            raise RuntimeError("forced commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="forced commit failure"):
            await ApprovalDecisionService(session).approve(
                workspace=workspace,
                approval_id=approval_id,
                reviewer_note="Should roll back",
                actor=actor,
            )
    finally:
        session.close()

    stored = _approval(approval_id)
    assert stored.status is ApprovalStatus.PENDING
    assert stored.reviewer_note is None
    assert stored.decided_at is None
    assert stored.decided_by_user_id is None
    assert stored.decided_by_membership_id is None
    assert stored.decided_by_role is None
    assert _message_count() == 0
