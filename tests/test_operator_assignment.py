from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import (
    AIInvocationUsage,
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    Lead,
    SalesConversationHandoff,
    SalesConversationHandoffStatus,
    SalesHandoffReasonCode,
    SalesStage,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.services.authentication import AuthenticationService
from app.services.operator_assignments import OperatorAssignmentActor, OperatorAssignmentService


TEST_PASSWORD = "correct-password"


def _settings():
    return app.dependency_overrides[get_settings]()


def _session():
    return next(app.dependency_overrides[get_session]())


def _new_user(email: str, display_name: str | None = None) -> tuple[UUID, str]:
    with _session() as session:
        service = AuthenticationService(session, _settings())
        user = service.register(email=email, password=TEST_PASSWORD, display_name=display_name)
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
            "email": f"{slug}@example.com",
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


def _set_user_active(user_id: UUID, active: bool) -> None:
    with _session() as session:
        user = session.get(User, user_id)
        assert user is not None
        user.active = active
        session.add(user)
        session.commit()


def _lead(lead_id: UUID) -> Lead:
    with _session() as session:
        lead = session.get(Lead, lead_id)
        assert lead is not None
        session.expunge(lead)
        return lead


def _approval(approval_id: UUID) -> ApprovalRequest:
    with _session() as session:
        approval = session.get(ApprovalRequest, approval_id)
        assert approval is not None
        session.expunge(approval)
        return approval


def _create_approval(lead_id: UUID, status: ApprovalStatus = ApprovalStatus.PENDING) -> UUID:
    with _session() as session:
        approval = ApprovalRequest(
            lead_id=lead_id,
            channel="console",
            payload={"recipient": "customer", "content": "Needs review"},
            status=status,
        )
        session.add(approval)
        session.commit()
        return approval.id


def _counts() -> dict[str, int]:
    with _session() as session:
        return {
            "messages": len(session.exec(select(ConversationMessage)).all()),
            "ai_usage": len(session.exec(select(AIInvocationUsage)).all()),
            "handoffs": len(session.exec(select(SalesConversationHandoff)).all()),
            "approvals": len(session.exec(select(ApprovalRequest)).all()),
        }


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


def test_owner_can_assign_conversation_to_active_member_and_metadata_is_exposed(client):
    owner_id, owner_token = _new_user("assign-owner@example.com")
    member_id, _ = _new_user("assign-member@example.com", "Assigned Member")
    workspace_id = _create_workspace(client, "assign-lead", owner_token)
    owner_membership = _membership(workspace_id, owner_id)
    member_membership_id = _add_membership(
        workspace_id,
        member_id,
        WorkspaceMemberRole.MEMBER,
    )
    lead_id = _create_lead(client, "assign-lead", owner_token)

    response = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers={
            **_workspace_headers("assign-lead", owner_token),
            "X-User-Id": str(member_id),
        },
        json={"workspace_member_id": str(member_membership_id)},
    )
    detail = client.get(
        f"/api/leads/{lead_id}",
        headers=_workspace_headers("assign-lead", owner_token),
    )

    assignment = response.json()["assignment"]
    stored = _lead(lead_id)
    assert response.status_code == 200
    assert assignment["assigned_to_membership_id"] == str(member_membership_id)
    assert assignment["assigned_to_user_id"] == str(member_id)
    assert assignment["assigned_to_display_name"] == "Assigned Member"
    assert assignment["assigned_by_user_id"] == str(owner_id)
    assert assignment["assigned_by_membership_id"] == str(owner_membership.id)
    assert assignment["assignee_membership_active"] is True
    assert assignment["assignee_user_active"] is True
    assert detail.json()["assignment"] == assignment
    assert stored.assigned_to_membership_id == member_membership_id
    assert stored.assigned_by_user_id == owner_id
    assert {"email", "password", "password_hash", "token", "credential"}.isdisjoint(assignment)


def test_admin_can_assign_approval_and_decision_actor_may_differ(client):
    owner_id, owner_token = _new_user("assign-approval-owner@example.com")
    admin_id, admin_token = _new_user("assign-approval-admin@example.com")
    member_id, member_token = _new_user("assign-approval-member@example.com")
    workspace_id = _create_workspace(client, "assign-approval", owner_token)
    admin_membership_id = _add_membership(workspace_id, admin_id, WorkspaceMemberRole.ADMIN)
    member_membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    lead_id = _create_lead(client, "assign-approval", owner_token)
    approval_id = _create_approval(lead_id)

    assigned = client.put(
        f"/api/approvals/{approval_id}/assignment",
        headers=_workspace_headers("assign-approval", admin_token),
        json={"workspace_member_id": str(member_membership_id)},
    )
    member_decision = client.post(
        f"/api/approvals/{approval_id}/reject",
        headers=_workspace_headers("assign-approval", member_token),
        json={},
    )
    owner_decision = client.post(
        f"/api/approvals/{approval_id}/reject",
        headers=_workspace_headers("assign-approval", owner_token),
        json={"reviewer_note": "Owner decides despite assignment"},
    )

    stored = _approval(approval_id)
    assert assigned.status_code == 200
    assert assigned.json()["assignment"]["assigned_to_membership_id"] == str(
        member_membership_id
    )
    assert assigned.json()["assignment"]["assigned_by_membership_id"] == str(
        admin_membership_id
    )
    assert member_decision.status_code == 403
    assert owner_decision.status_code == 200
    assert stored.assigned_to_membership_id == member_membership_id
    assert stored.assigned_by_membership_id == admin_membership_id
    assert stored.decided_by_user_id == owner_id
    assert stored.decided_by_membership_id == _membership(workspace_id, owner_id).id
    assert stored.decided_by_membership_id != stored.assigned_to_membership_id


def test_member_cannot_arbitrarily_assign_and_missing_authentication_is_401(client):
    owner_id, owner_token = _new_user("assign-deny-owner@example.com")
    member_id, member_token = _new_user("assign-deny-member@example.com")
    target_id, _ = _new_user("assign-deny-target@example.com")
    workspace_id = _create_workspace(client, "assign-deny", owner_token)
    _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    target_membership_id = _add_membership(workspace_id, target_id, WorkspaceMemberRole.MEMBER)
    lead_id = _create_lead(client, "assign-deny", owner_token)

    with TestClient(app) as public_client:
        unauthenticated = public_client.put(
            f"/api/conversations/{lead_id}/assignment",
            headers={"X-Workspace-Slug": "assign-deny"},
            json={"workspace_member_id": str(target_membership_id)},
        )
    denied = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers={
            **_workspace_headers("assign-deny", member_token),
            "X-Workspace-Role": "owner",
        },
        json={"workspace_member_id": str(target_membership_id)},
    )

    stored = _lead(lead_id)
    assert owner_id != member_id
    assert unauthenticated.status_code == 401
    assert denied.status_code == 403
    assert stored.assigned_to_membership_id is None


def test_workspace_isolation_and_target_validation_use_safe_not_found(client):
    owner_id, owner_token = _new_user("assign-safe-owner@example.com")
    outsider_id, outsider_token = _new_user("assign-safe-outsider@example.com")
    target_id, _ = _new_user("assign-safe-target@example.com")
    inactive_member_user_id, _ = _new_user("assign-inactive-member@example.com")
    inactive_user_id, _ = _new_user("assign-inactive-user@example.com")
    workspace_a_id = _create_workspace(client, "assign-safe-a", owner_token)
    workspace_b_id = _create_workspace(client, "assign-safe-b", owner_token)
    workspace_c_id = _create_workspace(client, "assign-safe-c", outsider_token)
    target_a = _add_membership(workspace_a_id, target_id, WorkspaceMemberRole.MEMBER)
    target_b = _add_membership(workspace_b_id, target_id, WorkspaceMemberRole.MEMBER)
    inactive_membership = _add_membership(
        workspace_a_id,
        inactive_member_user_id,
        WorkspaceMemberRole.MEMBER,
        active=False,
    )
    inactive_user_membership = _add_membership(
        workspace_a_id,
        inactive_user_id,
        WorkspaceMemberRole.MEMBER,
    )
    _set_user_active(inactive_user_id, False)
    lead_a = _create_lead(client, "assign-safe-a", owner_token)
    lead_c = _create_lead(client, "assign-safe-c", outsider_token)

    allowed = client.put(
        f"/api/conversations/{lead_a}/assignment",
        headers=_workspace_headers("assign-safe-a", owner_token),
        json={"workspace_member_id": str(target_a)},
    )
    unknown_target = client.put(
        f"/api/conversations/{lead_a}/assignment",
        headers=_workspace_headers("assign-safe-a", owner_token),
        json={"workspace_member_id": str(uuid4())},
    )
    cross_target = client.put(
        f"/api/conversations/{lead_a}/assignment",
        headers=_workspace_headers("assign-safe-a", owner_token),
        json={"workspace_member_id": str(target_b)},
    )
    inactive_target = client.put(
        f"/api/conversations/{lead_a}/assignment",
        headers=_workspace_headers("assign-safe-a", owner_token),
        json={"workspace_member_id": str(inactive_membership)},
    )
    inactive_user_target = client.put(
        f"/api/conversations/{lead_a}/assignment",
        headers=_workspace_headers("assign-safe-a", owner_token),
        json={"workspace_member_id": str(inactive_user_membership)},
    )
    no_membership = client.put(
        f"/api/conversations/{lead_a}/assignment",
        headers=_workspace_headers("assign-safe-a", outsider_token),
        json={"workspace_member_id": str(target_a)},
    )
    cross_workspace_actor = client.put(
        f"/api/conversations/{lead_c}/assignment",
        headers=_workspace_headers("assign-safe-c", owner_token),
        json={"workspace_member_id": str(target_a)},
    )

    assert workspace_b_id != workspace_c_id
    assert _membership(workspace_a_id, owner_id).role is WorkspaceMemberRole.OWNER
    assert allowed.status_code == 200
    assert unknown_target.status_code == 404
    assert cross_target.status_code == 404
    assert inactive_target.status_code == 404
    assert inactive_user_target.status_code == 404
    assert no_membership.status_code == 404
    assert cross_workspace_actor.status_code == 404
    assert _lead(lead_a).assigned_to_membership_id == target_a


def test_assignment_actor_comes_from_trusted_context_not_body_headers_or_jwt_claims(client):
    owner_id, owner_token = _new_user("assign-trusted-owner@example.com")
    admin_id, admin_token = _new_user("assign-trusted-admin@example.com")
    member_id, member_token = _new_user("assign-trusted-member@example.com")
    target_id, _ = _new_user("assign-trusted-target@example.com")
    workspace_id = _create_workspace(client, "assign-trusted", owner_token)
    admin_membership_id = _add_membership(workspace_id, admin_id, WorkspaceMemberRole.ADMIN)
    member_membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    target_membership_id = _add_membership(workspace_id, target_id, WorkspaceMemberRole.MEMBER)
    lead_id = _create_lead(client, "assign-trusted", owner_token)
    forged_member_token = _forged_token(
        member_id,
        role="owner",
        workspace_role="owner",
        workspace_id=str(workspace_id),
        permissions=["operator_assignment_manage"],
    )

    body_actor = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-trusted", owner_token),
        json={
            "workspace_member_id": str(target_membership_id),
            "assigned_by_user_id": str(member_id),
        },
    )
    forged_denied = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-trusted", forged_member_token),
        json={"workspace_member_id": str(target_membership_id)},
    )
    assigned = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers={
            **_workspace_headers("assign-trusted", admin_token),
            "X-Assigned-By-User-Id": str(owner_id),
            "X-Workspace-Role": "owner",
        },
        json={"workspace_member_id": str(target_membership_id)},
    )
    _set_membership_role(admin_membership_id, WorkspaceMemberRole.MEMBER)
    demoted = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-trusted", admin_token),
        json={"workspace_member_id": str(member_membership_id)},
    )

    stored = _lead(lead_id)
    assert body_actor.status_code == 422
    assert forged_denied.status_code == 403
    assert assigned.status_code == 200
    assert assigned.json()["assignment"]["assigned_by_user_id"] == str(admin_id)
    assert assigned.json()["assignment"]["assigned_by_membership_id"] == str(
        admin_membership_id
    )
    assert demoted.status_code == 403
    assert stored.assigned_by_user_id == admin_id
    assert stored.assigned_by_membership_id == admin_membership_id
    assert member_membership_id != target_membership_id


def test_conversation_assignment_is_metadata_only_and_does_not_touch_ai_or_handoff(client):
    owner_id, owner_token = _new_user("assign-metadata-owner@example.com")
    member_id, _ = _new_user("assign-metadata-member@example.com")
    workspace_id = _create_workspace(client, "assign-metadata", owner_token)
    member_membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    lead_id = _create_lead(client, "assign-metadata", owner_token)
    with _session() as session:
        workspace = session.get(Workspace, workspace_id)
        lead = session.get(Lead, lead_id)
        assert workspace is not None and lead is not None
        lead.sales_stage = SalesStage.DISCOVERY
        session.add(lead)
        session.add(
            SalesConversationHandoff(
                workspace_id=workspace_id,
                lead_id=lead_id,
                reason_code=SalesHandoffReasonCode.HUMAN_REQUESTED,
                explanation="A human should take over.",
            )
        )
        session.add(
            ConversationMessage(
                lead_id=lead_id,
                direction="inbound",
                channel="console",
                stage=SalesStage.DISCOVERY,
                content="Existing message",
            )
        )
        session.commit()
    before = _counts()

    response = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-metadata", owner_token),
        json={"workspace_member_id": str(member_membership_id)},
    )

    after = _counts()
    stored = _lead(lead_id)
    with _session() as session:
        handoff = session.exec(select(SalesConversationHandoff)).one()
        assert handoff.status is SalesConversationHandoffStatus.ACTIVE
    assert owner_id != member_id
    assert response.status_code == 200
    assert before == after
    assert stored.sales_stage is SalesStage.DISCOVERY
    assert stored.assigned_to_membership_id == member_membership_id


def test_reassignment_replaces_current_responsibility_and_unassignment_is_idempotent(client):
    _, owner_token = _new_user("assign-replace-owner@example.com")
    alice_id, _ = _new_user("assign-replace-alice@example.com")
    bob_id, _ = _new_user("assign-replace-bob@example.com")
    workspace_id = _create_workspace(client, "assign-replace", owner_token)
    alice_membership_id = _add_membership(workspace_id, alice_id, WorkspaceMemberRole.MEMBER)
    bob_membership_id = _add_membership(workspace_id, bob_id, WorkspaceMemberRole.MEMBER)
    lead_id = _create_lead(client, "assign-replace", owner_token)

    first = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-replace", owner_token),
        json={"workspace_member_id": str(alice_membership_id)},
    )
    second = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-replace", owner_token),
        json={"workspace_member_id": str(bob_membership_id)},
    )
    cleared = client.delete(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-replace", owner_token),
    )
    cleared_again = client.delete(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-replace", owner_token),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["assignment"]["assigned_to_membership_id"] == str(
        bob_membership_id
    )
    assert cleared.status_code == 200
    assert cleared.json()["assignment"] is None
    assert cleared_again.status_code == 200
    assert cleared_again.json()["assignment"] is None
    assert _lead(lead_id).assigned_to_membership_id is None


def test_terminal_approval_assignment_conflicts_and_decision_actor_is_not_rewritten(client):
    owner_id, owner_token = _new_user("assign-terminal-owner@example.com")
    member_id, _ = _new_user("assign-terminal-member@example.com")
    workspace_id = _create_workspace(client, "assign-terminal", owner_token)
    member_membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    lead_id = _create_lead(client, "assign-terminal", owner_token)
    approval_id = _create_approval(lead_id)

    assigned = client.put(
        f"/api/approvals/{approval_id}/assignment",
        headers=_workspace_headers("assign-terminal", owner_token),
        json={"workspace_member_id": str(member_membership_id)},
    )
    decided = client.post(
        f"/api/approvals/{approval_id}/reject",
        headers=_workspace_headers("assign-terminal", owner_token),
        json={"reviewer_note": "Terminal decision"},
    )
    reassign_terminal = client.put(
        f"/api/approvals/{approval_id}/assignment",
        headers=_workspace_headers("assign-terminal", owner_token),
        json={"workspace_member_id": str(member_membership_id)},
    )
    clear_terminal = client.delete(
        f"/api/approvals/{approval_id}/assignment",
        headers=_workspace_headers("assign-terminal", owner_token),
    )

    stored = _approval(approval_id)
    assert assigned.status_code == 200
    assert decided.status_code == 200
    assert reassign_terminal.status_code == 409
    assert clear_terminal.status_code == 409
    assert stored.status is ApprovalStatus.REJECTED
    assert stored.assigned_to_membership_id == member_membership_id
    assert stored.decided_by_user_id == owner_id
    assert stored.decided_by_membership_id == _membership(workspace_id, owner_id).id


def test_later_assignee_deactivation_preserves_assignment_metadata_on_reads(client):
    _, owner_token = _new_user("assign-deactivate-owner@example.com")
    member_id, _ = _new_user("assign-deactivate-member@example.com")
    workspace_id = _create_workspace(client, "assign-deactivate", owner_token)
    member_membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    lead_id = _create_lead(client, "assign-deactivate", owner_token)
    assert client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-deactivate", owner_token),
        json={"workspace_member_id": str(member_membership_id)},
    ).status_code == 200

    _set_membership_active(member_membership_id, False)
    _set_user_active(member_id, False)
    detail = client.get(
        f"/api/leads/{lead_id}",
        headers=_workspace_headers("assign-deactivate", owner_token),
    )

    assignment = detail.json()["assignment"]
    assert detail.status_code == 200
    assert assignment["assigned_to_membership_id"] == str(member_membership_id)
    assert assignment["assigned_to_user_id"] == str(member_id)
    assert assignment["assignee_membership_active"] is False
    assert assignment["assignee_user_active"] is False


def test_legacy_unassigned_resources_remain_readable(client):
    _, owner_token = _new_user("assign-legacy-owner@example.com")
    _create_workspace(client, "assign-legacy", owner_token)
    lead_id = _create_lead(client, "assign-legacy", owner_token)
    approval_id = _create_approval(lead_id)

    lead_detail = client.get(
        f"/api/leads/{lead_id}",
        headers=_workspace_headers("assign-legacy", owner_token),
    )
    approvals = client.get(
        "/api/approvals",
        headers=_workspace_headers("assign-legacy", owner_token),
    )

    row = next(item for item in approvals.json() if item["id"] == str(approval_id))
    assert lead_detail.status_code == 200
    assert approvals.status_code == 200
    assert lead_detail.json()["assignment"] is None
    assert row["assignment"] is None


def test_outbound_approval_assignment_is_pending_responsibility_not_delivery(client):
    _, owner_token = _new_user("assign-outbound-owner@example.com")
    member_id, _ = _new_user("assign-outbound-member@example.com")
    workspace_id = _create_workspace(client, "assign-outbound", owner_token)
    member_membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    account = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers("assign-outbound", owner_token),
        json={
            "provider": "generic_hmac",
            "external_account_id": "assign-outbound",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    action = client.post(
        f"/api/integrations/accounts/{account['id']}/outbound-actions",
        headers=_workspace_headers("assign-outbound", owner_token),
        json={
            "external_target_id": "recipient",
            "action_type": "send_message",
            "content": "Needs approval",
            "idempotency_key": "assign-outbound-action",
            "requires_approval": True,
        },
    ).json()
    before = _counts()

    assigned = client.put(
        f"/api/approvals/{action['approval_request_id']}/assignment",
        headers=_workspace_headers("assign-outbound", owner_token),
        json={"workspace_member_id": str(member_membership_id)},
    )
    status = client.get(
        f"/api/integrations/accounts/{account['id']}/outbound-actions/{action['id']}/approval-status",
        headers=_workspace_headers("assign-outbound", owner_token),
    )

    after = _counts()
    stored = _approval(UUID(action["approval_request_id"]))
    assert assigned.status_code == 200
    assert assigned.json()["status"] == ApprovalStatus.PENDING.value
    assert assigned.json()["decided_by_user_id"] is None
    assert assigned.json()["assignment"]["assigned_to_membership_id"] == str(
        member_membership_id
    )
    assert status.json()["approval_status"] == ApprovalStatus.PENDING.value
    assert before == after
    assert stored.assigned_to_membership_id == member_membership_id
    assert stored.decided_by_user_id is None


def test_integration_account_credentials_cannot_manage_human_assignment(client):
    _, owner_token = _new_user("assign-machine-owner@example.com")
    member_id, _ = _new_user("assign-machine-member@example.com")
    workspace_id = _create_workspace(client, "assign-machine", owner_token)
    member_membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    lead_id = _create_lead(client, "assign-machine", owner_token)
    account = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers("assign-machine", owner_token),
        json={
            "provider": "generic_hmac",
            "external_account_id": "assign-machine",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()

    integration_key_as_bearer = client.put(
        f"/api/conversations/{lead_id}/assignment",
        headers=_workspace_headers("assign-machine", account["inbound_credential"]),
        json={"workspace_member_id": str(member_membership_id)},
    )

    assert integration_key_as_bearer.status_code == 401
    assert _lead(lead_id).assigned_to_membership_id is None


def test_assignment_service_rolls_back_current_assignment_when_commit_fails(client, monkeypatch):
    owner_id, owner_token = _new_user("assign-rollback-owner@example.com")
    member_id, _ = _new_user("assign-rollback-member@example.com")
    workspace_id = _create_workspace(client, "assign-rollback", owner_token)
    owner_membership = _membership(workspace_id, owner_id)
    member_membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    lead_id = _create_lead(client, "assign-rollback", owner_token)
    session = _session()

    try:
        workspace = session.get(Workspace, workspace_id)
        assert workspace is not None
        actor = OperatorAssignmentActor(
            user_id=owner_id,
            membership_id=owner_membership.id,
            workspace_id=workspace_id,
            role=WorkspaceMemberRole.OWNER,
        )

        def fail_commit():
            raise RuntimeError("forced assignment commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="forced assignment commit failure"):
            OperatorAssignmentService(session).assign_lead(
                workspace=workspace,
                lead_id=lead_id,
                target_membership_id=member_membership_id,
                actor=actor,
            )
    finally:
        session.close()

    assert _lead(lead_id).assigned_to_membership_id is None
