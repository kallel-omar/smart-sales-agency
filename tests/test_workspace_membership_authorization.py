from uuid import UUID

import jwt
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import get_settings
from app.db import get_session
from app.main import app
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
from app.models import (
    AIInvocationUsage,
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    Lead,
    SalesConversationHandoff,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
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
        user_id = user.id
        token = service.issue_access_token(user)
    return user_id, token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workspace_headers(slug: str, token: str) -> dict[str, str]:
    return {**_auth_headers(token), "X-Workspace-Slug": slug}


def _create_workspace(client, slug: str, token: str) -> dict:
    response = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.replace("-", " ").title()},
        headers=_auth_headers(token),
    )
    assert response.status_code == 201
    return response.json()


def _stored_workspace(slug: str) -> Workspace:
    with _session() as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
        session.expunge(workspace)
        return workspace


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


def _add_membership(
    *,
    workspace_id: UUID,
    user_id: UUID,
    role: WorkspaceMemberRole,
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


def _set_membership_active(membership_id: UUID, active: bool) -> None:
    with _session() as session:
        membership = session.get(WorkspaceMember, membership_id)
        assert membership is not None
        membership.active = active
        session.add(membership)
        session.commit()


def _create_lead(client, slug: str, token: str, *, tenant_id: str | None = None) -> dict:
    response = client.post(
        "/api/leads",
        headers=_workspace_headers(slug, token),
        json={
            "tenant_id": tenant_id or slug,
            "full_name": f"Lead {slug}",
            "company_name": f"{slug} Customer",
            "email": f"{slug}@example.com",
            "source": "manual",
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_product(client, slug: str, token: str, *, tenant_id: str | None = None) -> dict:
    response = client.post(
        "/api/products",
        headers=_workspace_headers(slug, token),
        json={
            "tenant_id": tenant_id or slug,
            "name": f"Product {slug}",
            "description": "Membership-scoped product.",
            "price": 99,
            "minimum_price": 80,
            "metadata_json": {},
        },
    )
    assert response.status_code == 201
    return response.json()


def _counts() -> dict[str, int]:
    with _session() as session:
        return {
            "messages": len(session.exec(select(ConversationMessage)).all()),
            "ai_usage": len(session.exec(select(AIInvocationUsage)).all()),
            "approvals": len(session.exec(select(ApprovalRequest)).all()),
            "handoffs": len(session.exec(select(SalesConversationHandoff)).all()),
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


def test_public_auth_routes_and_identity_route_boundaries(client):
    with TestClient(app) as public_client:
        registered = public_client.post(
            "/api/auth/register",
            json={"email": "public@example.com", "password": TEST_PASSWORD},
        )
        login = public_client.post(
            "/api/auth/login",
            json={"email": "public@example.com", "password": TEST_PASSWORD},
        )
        missing_me = public_client.get("/api/auth/me")

    assert registered.status_code == 201
    assert login.status_code == 200
    assert missing_me.status_code == 401

    me = client.get("/api/auth/me")
    assert me.status_code == 200


def test_workspace_creation_requires_authentication_and_bootstraps_owner(client):
    with TestClient(app) as public_client:
        unauthenticated = public_client.post(
            "/api/workspaces",
            json={"slug": "no-token", "name": "No Token"},
        )
    assert unauthenticated.status_code == 401

    user_id, token = _new_user("creator-281@example.com")
    workspace = _create_workspace(client, "creator-281", token)
    membership = _membership(UUID(workspace["id"]), user_id)

    assert membership.role == WorkspaceMemberRole.OWNER
    assert membership.active is True


def test_workspace_list_and_slug_read_are_membership_scoped(client):
    user_a, token_a = _new_user("list-a@example.com")
    _, token_b = _new_user("list-b@example.com")
    workspace_a = _create_workspace(client, "list-a", token_a)
    workspace_b = _create_workspace(client, "list-b", token_b)

    list_response = client.get("/api/workspaces", headers=_auth_headers(token_a))
    assert list_response.status_code == 200
    assert [workspace["slug"] for workspace in list_response.json()] == ["list-a"]

    assert client.get("/api/workspaces/list-a", headers=_auth_headers(token_a)).status_code == 200
    hidden = client.get("/api/workspaces/list-b", headers=_auth_headers(token_a))
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "Workspace not found"

    with _session() as session:
        memberships = session.exec(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == UUID(workspace_b["id"]))
        ).all()
    assert all(membership.user_id != user_a for membership in memberships)


def test_protected_workspace_route_authentication_and_membership_denials(client):
    user_a, token_a = _new_user("gate-a@example.com")
    _, token_b = _new_user("gate-b@example.com")
    _create_workspace(client, "gate-a", token_a)
    _create_workspace(client, "gate-b", token_b)

    with TestClient(app) as public_client:
        missing_token = public_client.get(
            "/api/leads",
            headers={"X-Workspace-Slug": "gate-a"},
        )
    invalid_token = client.get(
        "/api/leads",
        headers=_workspace_headers("gate-a", "not-a-jwt"),
    )
    no_membership = client.get("/api/leads", headers=_workspace_headers("gate-b", token_a))
    unknown_workspace = client.get(
        "/api/leads",
        headers=_workspace_headers("missing-workspace", token_a),
    )

    assert missing_token.status_code == 401
    assert invalid_token.status_code == 401
    assert no_membership.status_code == 404
    assert unknown_workspace.status_code == 404

    workspace_a = _stored_workspace("gate-a")
    with _session() as session:
        user = session.get(User, user_a)
        assert user is not None
        assert session.get(Workspace, workspace_a.id) is not None


def test_inactive_and_reactivated_membership_is_resolved_from_persistence(client):
    user_id, token = _new_user("reactivated-member@example.com")
    workspace = _create_workspace(client, "reactivated-membership", token)
    membership = _membership(UUID(workspace["id"]), user_id)

    _set_membership_active(membership.id, False)
    denied = client.get(
        "/api/leads",
        headers=_workspace_headers("reactivated-membership", token),
    )
    assert denied.status_code == 404

    _set_membership_active(membership.id, True)
    allowed = client.get(
        "/api/leads",
        headers=_workspace_headers("reactivated-membership", token),
    )
    assert allowed.status_code == 200


def test_basic_membership_gate_does_not_differentiate_owner_admin_member(client):
    owner_id, owner_token = _new_user("role-owner@example.com")
    admin_id, admin_token = _new_user("role-admin@example.com")
    member_id, member_token = _new_user("role-member@example.com")
    workspace = _create_workspace(client, "role-gate", owner_token)
    workspace_id = UUID(workspace["id"])

    _add_membership(
        workspace_id=workspace_id,
        user_id=admin_id,
        role=WorkspaceMemberRole.ADMIN,
    )
    _add_membership(
        workspace_id=workspace_id,
        user_id=member_id,
        role=WorkspaceMemberRole.MEMBER,
    )

    role_tokens = {
        WorkspaceMemberRole.OWNER: (owner_id, owner_token),
        WorkspaceMemberRole.ADMIN: (admin_id, admin_token),
        WorkspaceMemberRole.MEMBER: (member_id, member_token),
    }
    for role, (_, token) in role_tokens.items():
        response = client.get("/api/leads", headers=_workspace_headers("role-gate", token))
        assert response.status_code == 200, role


def test_same_user_can_select_multiple_member_workspaces_independently(client):
    user_id, token = _new_user("multi-member@example.com")
    workspace_a = _create_workspace(client, "multi-a", token)
    workspace_b = _create_workspace(client, "multi-b", token)

    lead_a = _create_lead(client, "multi-a", token)
    lead_b = _create_lead(client, "multi-b", token)

    a_response = client.get("/api/leads", headers=_workspace_headers("multi-a", token))
    b_response = client.get("/api/leads", headers=_workspace_headers("multi-b", token))

    assert [lead["id"] for lead in a_response.json()] == [lead_a["id"]]
    assert [lead["id"] for lead in b_response.json()] == [lead_b["id"]]
    assert _membership(UUID(workspace_a["id"]), user_id).active is True
    assert _membership(UUID(workspace_b["id"]), user_id).active is True


def test_body_tenant_id_cannot_override_selected_workspace_for_leads_or_products(client):
    _, token = _new_user("body-override@example.com")
    _create_workspace(client, "body-a", token)
    _create_workspace(client, "body-b", token)

    lead = _create_lead(client, "body-a", token, tenant_id="body-b")
    product = _create_product(client, "body-a", token, tenant_id="body-b")

    assert lead["tenant_id"] == "body-a"
    assert product["tenant_id"] == "body-a"
    assert client.get("/api/leads", headers=_workspace_headers("body-b", token)).json() == []
    assert client.get("/api/products", headers=_workspace_headers("body-b", token)).json() == []


def test_cross_workspace_reads_and_writes_are_denied_without_data_or_mutation(client):
    _, token_a = _new_user("cross-a@example.com")
    _, token_b = _new_user("cross-b@example.com")
    _create_workspace(client, "cross-a", token_a)
    _create_workspace(client, "cross-b", token_b)
    _create_lead(client, "cross-b", token_b)
    before_leads = client.get("/api/leads", headers=_workspace_headers("cross-b", token_b)).json()

    read_denied = client.get("/api/leads", headers=_workspace_headers("cross-b", token_a))
    write_denied = client.post(
        "/api/products",
        headers=_workspace_headers("cross-b", token_a),
        json={
            "tenant_id": "cross-b",
            "name": "Forbidden Product",
            "description": "Must not persist.",
            "price": 1,
            "minimum_price": 1,
            "metadata_json": {},
        },
    )
    after_leads = client.get("/api/leads", headers=_workspace_headers("cross-b", token_b)).json()
    after_products = client.get(
        "/api/products",
        headers=_workspace_headers("cross-b", token_b),
    ).json()

    assert read_denied.status_code == 404
    assert write_denied.status_code == 404
    assert after_leads == before_leads
    assert after_products == []


def test_unauthorized_conversation_request_invokes_no_ai_and_creates_no_records(client):
    _, token_a = _new_user("conversation-a@example.com")
    _, token_b = _new_user("conversation-b@example.com")
    _create_workspace(client, "conversation-a", token_a)
    _create_workspace(client, "conversation-b", token_b)
    lead_b = _create_lead(client, "conversation-b", token_b)
    before = _counts()

    response = client.post(
        f"/api/conversations/{lead_b['id']}/reply",
        headers=_workspace_headers("conversation-b", token_a),
        json={"content": "Can you give me a discount?", "channel": "console"},
    )

    assert response.status_code == 404
    assert _counts() == before


def test_unauthorized_approval_request_does_not_change_state(client):
    _, token_a = _new_user("approval-a@example.com")
    _, token_b = _new_user("approval-b@example.com")
    _create_workspace(client, "approval-a", token_a)
    _create_workspace(client, "approval-b", token_b)
    with _session() as session:
        lead = Lead(
            tenant_id="approval-b",
            full_name="Approval Lead",
            company_name="Approval Company",
        )
        session.add(lead)
        session.commit()
        session.refresh(lead)
        approval = ApprovalRequest(
            lead_id=lead.id,
            action_type="send_message",
            channel="console",
            payload={"recipient": "customer", "content": "Hello"},
        )
        session.add(approval)
        session.commit()
        approval_id = approval.id

    response = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=_workspace_headers("approval-b", token_a),
        json={"reviewer_note": "should not apply"},
    )

    with _session() as session:
        stored = session.get(ApprovalRequest, approval_id)
        assert stored is not None
        assert stored.status == ApprovalStatus.PENDING
        assert stored.reviewer_note is None
        assert stored.decided_at is None
    assert response.status_code == 404


def test_token_claims_remain_identity_only_and_membership_is_not_jwt_authority(client):
    user_id, token = _new_user("claims@example.com")
    workspace = _create_workspace(client, "claims-workspace", token)
    claims = jwt.decode(token, options={"verify_signature": False})

    assert claims["sub"] == str(user_id)
    assert not {"workspace_id", "workspace_slug", "role", "permissions"} & set(claims)

    membership = _membership(UUID(workspace["id"]), user_id)
    _set_membership_active(membership.id, False)
    response = client.get(
        "/api/leads",
        headers=_workspace_headers("claims-workspace", token),
    )
    assert response.status_code == 404


def test_machine_integration_auth_remains_separate_from_human_bearer(
    client,
    signed_webhook_request,
):
    _, token = _new_user("machine-separation@example.com")
    _create_workspace(client, "machine-separation", token)
    lead = _create_lead(client, "machine-separation", token)
    account = client.post(
        "/api/integrations/accounts",
        headers=_workspace_headers("machine-separation", token),
        json={
            "provider": "generic_hmac",
            "external_account_id": "machine-separation",
            "secret_reference": "INTEGRATION_SECRET_GENERIC_HMAC_TEST",
        },
    ).json()
    integration_key = account["inbound_credential"]
    headers, body = signed_webhook_request(
        integration_key,
        {
            "lead_id": lead["id"],
            "channel": "console",
            "content": "Hello from the provider",
            "external_event_id": "machine-separation-1",
        },
    )

    with TestClient(app) as machine_client:
        machine_response = machine_client.post(
            "/api/integrations/inbound-events",
            headers=headers,
            content=body,
        )
        bearer_only = machine_client.post(
            "/api/integrations/inbound-events",
            headers=_auth_headers(token),
            json={
                "lead_id": lead["id"],
                "channel": "console",
                "content": "Bearer is not an integration credential",
            },
        )

    integration_key_as_bearer = client.get(
        "/api/auth/me",
        headers=_auth_headers(integration_key),
    )

    assert machine_response.status_code == 200
    assert bearer_only.status_code != 200
    assert integration_key_as_bearer.status_code == 401


def test_integration_management_and_ai_usage_routes_are_membership_protected(client):
    _, token_a = _new_user("integration-human-a@example.com")
    _, token_b = _new_user("integration-human-b@example.com")
    _create_workspace(client, "integration-human-a", token_a)
    _create_workspace(client, "integration-human-b", token_b)

    with TestClient(app) as public_client:
        missing_token = public_client.get(
            "/api/integrations/accounts",
            headers={"X-Workspace-Slug": "integration-human-b"},
        )
    accounts_denied = client.get(
        "/api/integrations/accounts",
        headers=_workspace_headers("integration-human-b", token_a),
    )
    ai_usage_denied = client.get(
        "/api/integrations/ai-usage",
        headers=_workspace_headers("integration-human-b", token_a),
    )

    assert missing_token.status_code == 401
    assert accounts_denied.status_code == 404
    assert ai_usage_denied.status_code == 404


def test_known_human_workspace_routes_depend_on_membership_context():
    workspace_context_routes = {
        ("GET", "/api/leads"),
        ("POST", "/api/leads"),
        ("GET", "/api/leads/{lead_id}"),
        ("GET", "/api/products"),
        ("POST", "/api/products"),
        ("GET", "/api/conversations/{lead_id}"),
        ("PUT", "/api/conversations/{lead_id}/assignment"),
        ("DELETE", "/api/conversations/{lead_id}/assignment"),
        ("POST", "/api/conversations/{lead_id}/reply"),
        ("POST", "/api/conversations/{lead_id}/handoff/resolve"),
        ("POST", "/api/workflows/{lead_id}/run"),
        ("GET", "/api/approvals"),
        ("PUT", "/api/approvals/{approval_id}/assignment"),
        ("DELETE", "/api/approvals/{approval_id}/assignment"),
        ("POST", "/api/approvals/{approval_id}/approve"),
        ("POST", "/api/approvals/{approval_id}/reject"),
        ("GET", "/api/workspaces/sales-instructions"),
        ("PUT", "/api/workspaces/sales-instructions"),
        ("DELETE", "/api/workspaces/sales-instructions"),
        ("GET", "/api/workspaces/sales-communication"),
        ("PUT", "/api/workspaces/sales-communication"),
        ("GET", "/api/integrations/outbound-audit-events"),
        ("POST", "/api/integrations/accounts"),
        ("GET", "/api/integrations/accounts"),
        ("GET", "/api/integrations/operational-summary"),
        ("GET", "/api/integrations/ai-usage/summary"),
        ("GET", "/api/integrations/ai-usage"),
        ("GET", "/api/integrations/accounts/{account_id}/health"),
        ("GET", "/api/integrations/accounts/{account_id}/health/runtime-readiness"),
        ("GET", "/api/integrations/accounts/{account_id}/audit-events"),
        ("GET", "/api/integrations/audit-events"),
        ("POST", "/api/integrations/audit-events/retention-cleanup"),
        ("POST", "/api/integrations/accounts/{account_id}/deactivate"),
        ("POST", "/api/integrations/accounts/{account_id}/reactivate"),
        ("POST", "/api/integrations/accounts/{account_id}/credential/rotate"),
        ("POST", "/api/integrations/accounts/{account_id}/secret-reference"),
        ("POST", "/api/integrations/accounts/{account_id}/outbound-actions"),
        ("GET", "/api/integrations/outbound-actions"),
        ("POST", "/api/integrations/outbound-actions/expiration-cleanup"),
        ("GET", "/api/integrations/outbound-actions/{action_id}"),
        ("POST", "/api/integrations/outbound-actions/{action_id}/annotations"),
        ("GET", "/api/integrations/outbound-actions/{action_id}/annotations"),
        ("POST", "/api/integrations/outbound-actions/{action_id}/labels"),
        ("GET", "/api/integrations/outbound-actions/{action_id}/labels"),
        ("DELETE", "/api/integrations/outbound-actions/{action_id}/labels/{label}"),
        ("PUT", "/api/integrations/outbound-actions/{action_id}/priority"),
        ("PUT", "/api/integrations/outbound-actions/{action_id}/owner-reference"),
        ("POST", "/api/integrations/outbound-actions/{action_id}/archive"),
        ("POST", "/api/integrations/outbound-actions/{action_id}/unarchive"),
        ("POST", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/deliver"),
        ("POST", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/cancel"),
        ("POST", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/retry"),
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/delivery-readiness"),
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/approval-status"),
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/state-history"),
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/timeline"),
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/transition-validation"),
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/delivery-status"),
        ("GET", "/api/integrations/accounts/{account_id}/outbound-actions/{action_id}/delivery-attempts"),
        ("GET", "/api/integrations/execution-traces/{correlation_id}"),
    }

    for method, path in workspace_context_routes:
        names = _dependency_names(_route(path, method))
        assert (
            "get_authenticated_workspace_context" in names
            or "get_authenticated_workspace_readiness_context" in names
        ), f"{method} {path}"

    assert "get_authenticated_principal" in _dependency_names(_route("/api/workspaces", "GET"))
    assert "get_authenticated_principal" in _dependency_names(_route("/api/workspaces/{slug}", "GET"))
    assert "get_authenticated_principal" in _dependency_names(_route("/api/auth/me", "GET"))
    assert "get_authenticated_principal" not in _dependency_names(
        _route("/api/auth/register", "POST")
    )
    assert "get_authenticated_principal" not in _dependency_names(
        _route("/api/auth/login", "POST")
    )

    inbound_dependencies = _dependency_names(_route("/api/integrations/inbound-events", "POST"))
    assert "get_verified_integration_context" in inbound_dependencies
    assert "get_authenticated_principal" not in inbound_dependencies
    assert "get_authenticated_workspace_context" not in inbound_dependencies
