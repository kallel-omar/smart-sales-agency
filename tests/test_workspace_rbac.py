from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.api.routes import (
    approvals_router,
    auth_router,
    conversations_router,
    integrations_router,
    leads_router,
    products_router,
    workflows_router,
    workspaces_router,
)
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import (
    AIInvocationUsage,
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    IntegrationAccount,
    Lead,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.services.authentication import AuthenticationService
from app.services.workspace_rbac import (
    UnknownWorkspaceRoleError,
    WorkspacePermission,
    WorkspaceRBACPolicy,
)


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


def _create_stored_workspace(slug: str) -> UUID:
    with _session() as session:
        workspace = Workspace(slug=slug, name=slug.replace("-", " ").title())
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        return workspace.id


def _create_lead(client, slug: str, token: str) -> dict:
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
    return response.json()


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


def _set_membership_role(membership_id: UUID, role: WorkspaceMemberRole | str) -> None:
    with _session() as session:
        membership = session.get(WorkspaceMember, membership_id)
        assert membership is not None
        membership.role = role  # type: ignore[assignment]
        session.add(membership)
        session.commit()


def _set_membership_active(membership_id: UUID, active: bool) -> None:
    with _session() as session:
        membership = session.get(WorkspaceMember, membership_id)
        assert membership is not None
        membership.active = active
        session.add(membership)
        session.commit()


def _create_approval(lead_id: UUID) -> UUID:
    with _session() as session:
        approval = ApprovalRequest(
            lead_id=lead_id,
            channel="console",
            payload={"recipient": "customer", "content": "Approved message"},
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


def _counts() -> dict[str, int]:
    with _session() as session:
        return {
            "messages": len(session.exec(select(ConversationMessage)).all()),
            "ai_usage": len(session.exec(select(AIInvocationUsage)).all()),
            "integrations": len(session.exec(select(IntegrationAccount)).all()),
        }


def _dependency_names(route) -> set[str]:
    names: set[str] = set()
    stack = list(getattr(route, "dependant", None).dependencies)
    while stack:
        dependency = stack.pop()
        names.add(getattr(dependency.call, "__name__", repr(dependency.call)))
        stack.extend(dependency.dependencies)
    return names


def _route(path: str, method: str):
    router_path = path.removeprefix("/api")
    for router in (
        approvals_router,
        auth_router,
        conversations_router,
        integrations_router,
        leads_router,
        products_router,
        workflows_router,
        workspaces_router,
    ):
        for route in router.routes:
            if (
                getattr(route, "path", None) == router_path
                and method in getattr(route, "methods", set())
            ):
                return route
    raise AssertionError(f"Route not found: {method} {path}")


def test_role_permission_policy_matrix_is_deterministic():
    all_permissions = frozenset(WorkspacePermission)
    member_permissions = frozenset(
        {
            WorkspacePermission.WORKSPACE_READ,
            WorkspacePermission.SALES_DATA_READ,
            WorkspacePermission.SALES_DATA_WRITE,
            WorkspacePermission.CONVERSATION_OPERATE,
        }
    )

    assert WorkspaceRBACPolicy.permissions_for_role(WorkspaceMemberRole.OWNER) == all_permissions
    assert WorkspaceRBACPolicy.permissions_for_role(WorkspaceMemberRole.ADMIN) == all_permissions
    assert WorkspaceRBACPolicy.permissions_for_role(WorkspaceMemberRole.MEMBER) == member_permissions
    assert WorkspaceRBACPolicy.allows(
        WorkspaceMemberRole.MEMBER,
        WorkspacePermission.SALES_DATA_WRITE,
    )
    assert not WorkspaceRBACPolicy.allows(
        WorkspaceMemberRole.MEMBER,
        WorkspacePermission.INTEGRATION_MANAGE,
    )
    assert not WorkspaceRBACPolicy.allows(
        "sales_manager",
        WorkspacePermission.WORKSPACE_READ,
    )
    with pytest.raises(UnknownWorkspaceRoleError):
        WorkspaceRBACPolicy.permissions_for_role("sales_manager")


def test_member_sales_access_and_sensitive_admin_denials(client):
    owner_id, owner_token = _new_user("rbac-owner@example.com")
    member_id, member_token = _new_user("rbac-member@example.com")
    workspace_id = _create_workspace(client, "rbac-sales", owner_token)
    _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    lead = _create_lead(client, "rbac-sales", member_token)

    workspace_read = client.get(
        "/api/workspaces/rbac-sales",
        headers=_auth_headers(member_token),
    )
    lead_list = client.get(
        "/api/leads",
        headers=_workspace_headers("rbac-sales", member_token),
    )
    conversation = client.post(
        f"/api/conversations/{lead['id']}/reply",
        headers=_workspace_headers("rbac-sales", member_token),
        json={"channel": "console", "content": "Can you help?"},
    )
    settings_update = client.put(
        "/api/workspaces/sales-instructions",
        headers=_workspace_headers("rbac-sales", member_token),
        json={"instructions": "Use a stricter workspace policy."},
    )
    integration_manage = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers("rbac-sales", member_token),
        json={
            "provider": "generic_hmac",
            "external_account_id": "rbac-sales",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    ai_usage = client.get(
        "/api/integrations/ai-usage",
        headers=_workspace_headers("rbac-sales", member_token),
    )

    assert owner_id != member_id
    assert workspace_read.status_code == 200
    assert lead_list.status_code == 200
    assert conversation.status_code == 200
    assert settings_update.status_code == 403
    assert integration_manage.status_code == 403
    assert ai_usage.status_code == 403
    assert _counts()["integrations"] == 0


def test_admin_and_owner_can_use_current_administrative_capabilities(client):
    _, owner_token = _new_user("rbac-admin-owner@example.com")
    admin_id, admin_token = _new_user("rbac-admin@example.com")
    workspace_id = _create_workspace(client, "rbac-admin", owner_token)
    _add_membership(workspace_id, admin_id, WorkspaceMemberRole.ADMIN)

    admin_settings = client.put(
        "/api/workspaces/sales-instructions",
        headers=_workspace_headers("rbac-admin", admin_token),
        json={"instructions": "Follow the approved admin sales policy."},
    )
    admin_integration = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers("rbac-admin", admin_token),
        json={
            "provider": "generic_hmac",
            "external_account_id": "rbac-admin",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    owner_ai_usage = client.get(
        "/api/integrations/ai-usage/summary",
        headers=_workspace_headers("rbac-admin", owner_token),
    )

    assert admin_settings.status_code == 200
    assert admin_settings.json()["sales_instructions"] == "Follow the approved admin sales policy."
    assert admin_integration.status_code == 201
    assert owner_ai_usage.status_code == 200


def test_approval_decision_requires_permission_and_member_attempt_does_not_mutate(client):
    _, owner_token = _new_user("rbac-approval-owner@example.com")
    member_id, member_token = _new_user("rbac-approval-member@example.com")
    workspace_id = _create_workspace(client, "rbac-approval", owner_token)
    _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    lead = _create_lead(client, "rbac-approval", owner_token)
    approval_id = _create_approval(UUID(lead["id"]))

    denied = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers={
            **_workspace_headers("rbac-approval", member_token),
            "X-Workspace-Role": "owner",
            "X-Workspace-Permission": "approval_decide",
        },
        json={
            "reviewer_note": "Approve me as owner",
            "role": "owner",
            "permission": "approval_decide",
        },
    )
    after_denied = _approval(approval_id)
    approved = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=_workspace_headers("rbac-approval", owner_token),
        json={"reviewer_note": "Owner approved"},
    )

    assert denied.status_code == 403
    assert after_denied.status is ApprovalStatus.PENDING
    assert after_denied.reviewer_note is None
    assert approved.status_code == 200
    assert approved.json()["status"] == ApprovalStatus.EXECUTED.value


def test_current_persisted_role_not_jwt_claim_controls_authorization(client):
    _, owner_token = _new_user("rbac-claims-owner@example.com")
    member_id, member_token = _new_user("rbac-claims-member@example.com")
    workspace_id = _create_workspace(client, "rbac-claims", owner_token)
    membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    settings = _settings()
    issued_claims = jwt.decode(
        member_token,
        settings.auth_token_secret.get_secret_value(),
        algorithms=[settings.auth_token_algorithm],
        issuer=settings.auth_token_issuer,
    )

    denied = client.put(
        "/api/workspaces/sales-instructions",
        headers=_workspace_headers("rbac-claims", member_token),
        json={"instructions": "Member should not manage settings."},
    )
    _set_membership_role(membership_id, WorkspaceMemberRole.ADMIN)
    allowed_same_token = client.put(
        "/api/workspaces/sales-instructions",
        headers=_workspace_headers("rbac-claims", member_token),
        json={"instructions": "Admin role is now persisted."},
    )
    _set_membership_role(membership_id, WorkspaceMemberRole.MEMBER)
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": str(member_id),
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iss": settings.auth_token_issuer,
            "role": "owner",
            "workspace_role": "owner",
            "workspace_id": str(workspace_id),
            "permissions": [WorkspacePermission.WORKSPACE_SETTINGS_MANAGE.value],
        },
        settings.auth_token_secret.get_secret_value(),
        algorithm=settings.auth_token_algorithm,
    )
    forged_denied = client.put(
        "/api/workspaces/sales-instructions",
        headers=_workspace_headers("rbac-claims", forged),
        json={"instructions": "JWT role is not authority."},
    )

    assert {"role", "workspace_role", "workspace_id", "permissions"}.isdisjoint(issued_claims)
    assert denied.status_code == 403
    assert allowed_same_token.status_code == 200
    assert forged_denied.status_code == 403


def test_authentication_and_membership_failures_stay_401_or_safe_404(client):
    _, owner_token = _new_user("rbac-boundary-owner@example.com")
    member_id, member_token = _new_user("rbac-boundary-member@example.com")
    outsider_id, outsider_token = _new_user("rbac-boundary-outsider@example.com")
    workspace_id = _create_workspace(client, "rbac-boundary-a", owner_token)
    workspace_b_id = _create_workspace(client, "rbac-boundary-b", owner_token)
    membership_id = _add_membership(workspace_id, member_id, WorkspaceMemberRole.MEMBER)
    _add_membership(workspace_b_id, outsider_id, WorkspaceMemberRole.MEMBER)

    with TestClient(app) as public_client:
        unauthenticated = public_client.put(
            "/api/workspaces/sales-instructions",
            headers={"X-Workspace-Slug": "rbac-boundary-a"},
            json={"instructions": "No bearer"},
        )
    no_membership = client.put(
        "/api/workspaces/sales-instructions",
        headers=_workspace_headers("rbac-boundary-a", outsider_token),
        json={"instructions": "No membership"},
    )
    _set_membership_active(membership_id, False)
    inactive_membership = client.put(
        "/api/workspaces/sales-instructions",
        headers=_workspace_headers("rbac-boundary-a", member_token),
        json={"instructions": "Inactive membership"},
    )
    cross_workspace = client.put(
        "/api/workspaces/sales-instructions",
        headers=_workspace_headers("rbac-boundary-b", member_token),
        json={"instructions": "Cross workspace"},
    )

    assert unauthenticated.status_code == 401
    assert no_membership.status_code == 404
    assert inactive_membership.status_code == 404
    assert cross_workspace.status_code == 404


def test_workspace_creation_and_listing_remain_special_and_role_agnostic(client):
    user_id, token = _new_user("rbac-list@example.com")
    owner_workspace_id = _create_workspace(client, "rbac-list-owner", token)
    admin_workspace_id = _create_stored_workspace("rbac-list-admin")
    member_workspace_id = _create_stored_workspace("rbac-list-member")
    _add_membership(admin_workspace_id, user_id, WorkspaceMemberRole.ADMIN)
    _add_membership(member_workspace_id, user_id, WorkspaceMemberRole.MEMBER)

    listed = client.get("/api/workspaces", headers=_auth_headers(token))

    assert listed.status_code == 200
    assert {workspace["slug"] for workspace in listed.json()} == {
        "rbac-list-owner",
        "rbac-list-admin",
        "rbac-list-member",
    }
    with _session() as session:
        owner_membership = session.exec(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == owner_workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        ).one()
        assert owner_membership.role is WorkspaceMemberRole.OWNER


def test_unauthorized_conversation_and_integration_management_do_not_mutate(client):
    _, owner_token = _new_user("rbac-no-mutate-owner@example.com")
    outsider_id, outsider_token = _new_user("rbac-no-mutate-outsider@example.com")
    workspace_id = _create_workspace(client, "rbac-no-mutate", owner_token)
    _add_membership(workspace_id, outsider_id, WorkspaceMemberRole.MEMBER)
    lead = _create_lead(client, "rbac-no-mutate", owner_token)
    before = _counts()
    _set_membership_active(
        _add_membership(
            _create_stored_workspace("rbac-no-mutate-other"),
            outsider_id,
            WorkspaceMemberRole.MEMBER,
        ),
        False,
    )

    conversation = client.post(
        f"/api/conversations/{lead['id']}/reply",
        headers=_workspace_headers("rbac-no-mutate-other", outsider_token),
        json={"channel": "console", "content": "This should not run."},
    )
    integration = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers("rbac-no-mutate", outsider_token),
        json={
            "provider": "generic_hmac",
            "external_account_id": "rbac-no-mutate",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    )
    after = _counts()

    assert conversation.status_code == 404
    assert integration.status_code == 403
    assert after == before


def test_machine_integration_route_stays_outside_human_rbac(client, signed_webhook_request):
    _, owner_token = _new_user("rbac-machine-owner@example.com")
    _create_workspace(client, "rbac-machine", owner_token)
    lead = _create_lead(client, "rbac-machine", owner_token)
    account = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers("rbac-machine", owner_token),
        json={
            "provider": "generic_hmac",
            "external_account_id": "rbac-machine",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    headers, body = signed_webhook_request(
        account["inbound_credential"],
        {
            "lead_id": lead["id"],
            "channel": "console",
            "content": "Provider-authenticated event",
            "external_event_id": "rbac-machine-1",
        },
    )

    with TestClient(app) as machine_client:
        machine_response = machine_client.post(
            "/api/integrations/inbound-events",
            headers=headers,
            content=body,
        )
    integration_key_as_bearer = client.get(
        "/api/integrations/accounts",
        headers=_workspace_headers("rbac-machine", account["inbound_credential"]),
    )

    assert machine_response.status_code == 200
    assert integration_key_as_bearer.status_code == 401


def test_known_human_workspace_routes_declare_exact_permissions():
    route_permissions = {
        ("GET", "/api/workspaces/{slug}"): "get_workspace_read_path_context",
        ("GET", "/api/workspaces/sales-instructions"): "require_workspace_read_permission",
        ("GET", "/api/workspaces/sales-communication"): "require_workspace_read_permission",
        ("PUT", "/api/workspaces/sales-instructions"): "require_workspace_settings_manage_permission",
        ("DELETE", "/api/workspaces/sales-instructions"): "require_workspace_settings_manage_permission",
        ("PUT", "/api/workspaces/sales-communication"): "require_workspace_settings_manage_permission",
        ("GET", "/api/leads"): "require_sales_data_read_permission",
        ("GET", "/api/leads/{lead_id}"): "require_sales_data_read_permission",
        ("POST", "/api/leads"): "require_sales_data_write_permission",
        ("GET", "/api/products"): "require_sales_data_read_permission",
        ("POST", "/api/products"): "require_sales_data_write_permission",
        ("GET", "/api/conversations/{lead_id}"): "require_conversation_operate_permission",
        ("POST", "/api/conversations/{lead_id}/reply"): "require_conversation_operate_permission",
        ("POST", "/api/conversations/{lead_id}/handoff/resolve"): "require_conversation_operate_permission",
        ("POST", "/api/workflows/{lead_id}/run"): "require_conversation_operate_permission",
        ("GET", "/api/approvals"): "require_sales_data_read_permission",
        ("POST", "/api/approvals/{approval_id}/approve"): "require_approval_decide_permission",
        ("POST", "/api/approvals/{approval_id}/reject"): "require_approval_decide_permission",
        ("GET", "/api/integrations/outbound-audit-events"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts"): "require_integration_read_permission",
        ("GET", "/api/integrations/operational-summary"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts/{account_id}/health"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts/{account_id}/health/runtime-readiness"): "require_integration_read_readiness_permission",
        ("GET", "/api/integrations/accounts/{account_id}/audit-events"): "require_integration_read_permission",
        ("GET", "/api/integrations/audit-events"): "require_integration_read_permission",
        ("GET", "/api/integrations/outbound-actions"): "require_integration_read_permission",
        ("GET", "/api/integrations/outbound-actions/{action_id}"): "require_integration_read_permission",
        ("GET", "/api/integrations/outbound-actions/{action_id}/annotations"): "require_integration_read_permission",
        ("GET", "/api/integrations/outbound-actions/{action_id}/labels"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/delivery-readiness"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/approval-status"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/state-history"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/timeline"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/transition-validation"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/delivery-status"): "require_integration_read_permission",
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/delivery-attempts"): "require_integration_read_permission",
        ("GET", "/api/integrations/execution-traces/{correlation_id}"): "require_integration_read_permission",
        ("GET", "/api/integrations/ai-usage/summary"): "require_ai_usage_read_permission",
        ("GET", "/api/integrations/ai-usage"): "require_ai_usage_read_permission",
        ("POST", "/api/integrations/accounts"): "require_integration_manage_permission",
        ("POST", "/api/integrations/audit-events/retention-cleanup"): "require_integration_manage_permission",
        ("POST", "/api/integrations/accounts/{account_id}/deactivate"): "require_integration_manage_permission",
        ("POST", "/api/integrations/accounts/{account_id}/reactivate"): "require_integration_manage_permission",
        ("POST", "/api/integrations/accounts/{account_id}/credential/rotate"): "require_integration_manage_permission",
        ("POST", "/api/integrations/accounts/{account_id}/secret-reference"): "require_integration_manage_permission",
        ("POST", "/api/integrations/outbound-actions/expiration-cleanup"): "require_integration_manage_permission",
        ("POST", "/api/integrations/accounts/{account_id}/outbound-actions"): "require_outbound_action_operate_permission",
        ("POST", "/api/integrations/outbound-actions/{action_id}/annotations"): "require_outbound_action_operate_permission",
        ("POST", "/api/integrations/outbound-actions/{action_id}/labels"): "require_outbound_action_operate_permission",
        ("DELETE", "/api/integrations/outbound-actions/{action_id}/labels/{label}"): "require_outbound_action_operate_permission",
        ("PUT", "/api/integrations/outbound-actions/{action_id}/priority"): "require_outbound_action_operate_permission",
        ("PUT", "/api/integrations/outbound-actions/{action_id}/owner-reference"): "require_outbound_action_operate_permission",
        ("POST", "/api/integrations/outbound-actions/{action_id}/archive"): "require_outbound_action_operate_permission",
        ("POST", "/api/integrations/outbound-actions/{action_id}/unarchive"): "require_outbound_action_operate_permission",
        ("POST", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/deliver"): "require_outbound_action_operate_permission",
        ("POST", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/cancel"): "require_outbound_action_operate_permission",
        ("POST", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/retry"): "require_outbound_action_operate_permission",
    }

    for (method, path), dependency_name in route_permissions.items():
        assert dependency_name in _dependency_names(_route(path, method)), f"{method} {path}"

    for method, path in {
        ("POST", "/api/auth/register"),
        ("POST", "/api/auth/login"),
        ("GET", "/api/auth/me"),
        ("POST", "/api/workspaces"),
        ("GET", "/api/workspaces"),
        ("POST", "/api/integrations/inbound-events"),
    }:
        names = _dependency_names(_route(path, method))
        assert not {
            name
            for name in names
            if name.startswith("require_") and name.endswith("_permission")
        }, f"{method} {path}"

    inbound_dependencies = _dependency_names(_route("/api/integrations/inbound-events", "POST"))
    assert "get_verified_integration_context" in inbound_dependencies
    assert "get_authenticated_workspace_context" not in inbound_dependencies
