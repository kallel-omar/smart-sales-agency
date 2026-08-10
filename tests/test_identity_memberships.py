from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.api.dependencies import get_current_workspace
from app.db import get_session
from app.main import app
from app.models import (
    ApprovalRequest,
    IntegrationAccount,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.schemas import UserRead, WorkspaceMemberCreate, WorkspaceMemberRead
from app.services.identity_memberships import (
    DuplicateUserEmailError,
    DuplicateWorkspaceMembershipError,
    IdentityMembershipService,
    InactiveUserError,
    InactiveWorkspaceMembershipError,
    UserIdentityValidationError,
    UserNotFoundError,
    WorkspaceMemberRoleValidationError,
    WorkspaceMembershipNotFoundError,
)
from app.services.workspaces import WorkspaceNotFoundError


def _workspace(client, slug: str) -> Workspace:
    response = client.post("/api/workspaces", json={"slug": slug, "name": slug.title()})
    assert response.status_code == 201
    with next(app.dependency_overrides[get_session]()) as session:
        workspace = session.get(Workspace, UUID(response.json()["id"]))
        assert workspace is not None
        return workspace


def _service(client) -> tuple[object, IdentityMembershipService]:
    session = next(app.dependency_overrides[get_session]())
    return session, IdentityMembershipService(session)


def test_registers_normalized_persisted_user_with_safe_schema(client):
    session, service = _service(client)
    try:
        user = service.register_user(
            email="  Operator@Example.COM  ",
            display_name="  Omar Kallel  ",
        )
        safe = UserRead.model_validate(user)
    finally:
        session.close()

    assert user.email == "operator@example.com"
    assert user.display_name == "Omar Kallel"
    assert user.active is True
    assert safe.model_dump().keys() == {
        "id",
        "email",
        "display_name",
        "active",
        "created_at",
        "updated_at",
    }
    assert not {"password", "token", "secret", "credential"} & set(safe.model_dump())


def test_invalid_and_duplicate_canonical_email_are_rejected_deterministically(client):
    session, service = _service(client)
    try:
        service.register_user(email="operator@example.com")
        with pytest.raises(DuplicateUserEmailError, match="already exists"):
            service.register_user(email=" OPERATOR@example.com ")
        with pytest.raises(UserIdentityValidationError, match="invalid"):
            service.register_user(email="not-an-email")
    finally:
        session.close()


def test_active_principal_resolves_but_inactive_identity_remains_persisted(client):
    session, service = _service(client)
    try:
        user = service.register_user(email="operator@example.com")
        principal = service.resolve_active_principal(user.id)
        service.deactivate_user(user.id)
        with pytest.raises(InactiveUserError, match="inactive"):
            service.resolve_active_principal(user.id)
        persisted = session.get(User, user.id)
    finally:
        session.close()

    assert principal.user_id == user.id
    assert principal.active is True
    assert persisted is not None
    assert persisted.active is False


def test_valid_memberships_persist_roles_and_allow_many_to_many_membership(client):
    workspace_a = _workspace(client, "members-a")
    workspace_b = _workspace(client, "members-b")
    session, service = _service(client)
    try:
        first_user = service.register_user(email="first@example.com")
        second_user = service.register_user(email="second@example.com")
        owner = service.add_membership(
            workspace=workspace_a,
            user_id=first_user.id,
            role=WorkspaceMemberRole.OWNER,
        )
        admin = service.add_membership(
            workspace=workspace_b,
            user_id=first_user.id,
            role=WorkspaceMemberRole.ADMIN,
        )
        member = service.add_membership(
            workspace=workspace_a,
            user_id=second_user.id,
            role=WorkspaceMemberRole.MEMBER,
        )
        safe = WorkspaceMemberRead.model_validate(owner)
        owner_role = owner.role
        admin_workspace_id = admin.workspace_id
        member_user_id = member.user_id
        workspace_b_id = workspace_b.id
        second_user_id = second_user.id
    finally:
        session.close()

    assert owner_role == WorkspaceMemberRole.OWNER
    assert admin_workspace_id == workspace_b_id
    assert member_user_id == second_user_id
    assert len([owner, admin]) == 2
    assert safe.role == WorkspaceMemberRole.OWNER


def test_membership_creation_rejects_duplicate_unknown_and_invalid_role(client):
    workspace = _workspace(client, "members-errors")
    session, service = _service(client)
    try:
        user = service.register_user(email="operator@example.com")
        service.add_membership(
            workspace=workspace,
            user_id=user.id,
            role=WorkspaceMemberRole.OWNER,
        )
        with pytest.raises(DuplicateWorkspaceMembershipError, match="already a member"):
            service.add_membership(
                workspace=workspace,
                user_id=user.id,
                role=WorkspaceMemberRole.ADMIN,
            )
        with pytest.raises(UserNotFoundError, match="User not found"):
            service.add_membership(
                workspace=workspace,
                user_id=uuid4(),
                role=WorkspaceMemberRole.MEMBER,
            )
        with pytest.raises(WorkspaceNotFoundError, match="Workspace not found"):
            service.add_membership(
                workspace=Workspace(slug="unknown", name="Unknown"),
                user_id=user.id,
                role=WorkspaceMemberRole.MEMBER,
            )
        with pytest.raises(WorkspaceMemberRoleValidationError, match="invalid"):
            service.add_membership(
                workspace=workspace,
                user_id=user.id,
                role="sales_manager",  # type: ignore[arg-type]
            )
    finally:
        session.close()


def test_active_membership_is_workspace_scoped_and_inactive_membership_cannot_resolve(client):
    workspace_a = _workspace(client, "members-scope-a")
    workspace_b = _workspace(client, "members-scope-b")
    session, service = _service(client)
    try:
        user = service.register_user(email="operator@example.com")
        membership = service.add_membership(
            workspace=workspace_a,
            user_id=user.id,
            role=WorkspaceMemberRole.MEMBER,
        )
        principal = service.resolve_active_principal(user.id)
        assert (
            service.resolve_active_membership(
                principal=principal,
                workspace=workspace_a,
            ).id
            == membership.id
        )
        with pytest.raises(WorkspaceMembershipNotFoundError, match="not found"):
            service.resolve_active_membership(principal=principal, workspace=workspace_b)
        membership.active = False
        session.add(membership)
        session.commit()
        with pytest.raises(InactiveWorkspaceMembershipError, match="inactive"):
            service.resolve_active_membership(principal=principal, workspace=workspace_a)
    finally:
        session.close()


def test_membership_payload_has_no_workspace_authority_and_no_http_user_header_auth_exists(client):
    with pytest.raises(ValidationError):
        WorkspaceMemberCreate(
            workspace_id=uuid4(),
            user_id=uuid4(),
            role=WorkspaceMemberRole.MEMBER,
        )

    dependencies_source = get_current_workspace.__module__
    assert dependencies_source == "app.api.dependencies"
    assert "X-User-Id" not in str(app.openapi() or {})
    assert not any(
        parameter.name.lower() in {"x-user-id", "x-authenticated-user-id"}
        for route in app.routes
        for parameter in getattr(getattr(route, "dependant", None), "header_params", [])
    )


def test_integration_account_remains_distinct_and_approval_has_no_retrofitted_actor(client):
    workspace = _workspace(client, "members-separation")
    session, service = _service(client)
    try:
        user = service.register_user(email="operator@example.com")
        user_id = user.id
        membership = service.add_membership(
            workspace=workspace,
            user_id=user_id,
            role=WorkspaceMemberRole.OWNER,
        )
        assert session.get(IntegrationAccount, membership.id) is None
        assert session.get(ApprovalRequest, membership.id) is None
        memberships = list(
            session.exec(
                select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
            )
        )
    finally:
        session.close()

    assert any(membership.user_id == user_id for membership in memberships)
