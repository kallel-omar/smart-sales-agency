from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import (
    User,
    UserPasswordCredential,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.services.identity_memberships import IdentityMembershipService

AUTH_URL = "/api/auth"


def _register(client, *, email: str = "operator@example.com", password: str = "correct-password"):
    return client.post(
        f"{AUTH_URL}/register",
        json={"email": email, "password": password, "display_name": "Operator"},
    )


def _login(client, *, email: str = "operator@example.com", password: str = "correct-password"):
    return client.post(
        f"{AUTH_URL}/login",
        json={"email": email, "password": password},
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _settings(client) -> Settings:
    return app.dependency_overrides[get_settings]()


def test_registration_persists_only_argon2_hash_and_safe_user_identity(client):
    response = _register(client)

    assert response.status_code == 201
    assert response.json().keys() == {
        "id",
        "email",
        "display_name",
        "active",
        "created_at",
        "updated_at",
    }
    assert "password" not in response.text
    assert "hash" not in response.text

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        user = session.get(User, UUID(response.json()["id"]))
        assert user is not None
        credential = session.exec(
            select(UserPasswordCredential).where(UserPasswordCredential.user_id == user.id)
        ).one()

    assert credential.password_hash.startswith("$argon2id$")
    assert credential.password_hash != "correct-password"
    assert "correct-password" not in credential.password_hash


def test_registration_rejects_duplicate_normalized_and_invalid_passwords(client):
    assert _register(client, email=" Operator@Example.com ").status_code == 201
    assert _register(client, email="operator@example.COM").status_code == 409

    blank = _register(client, email="blank@example.com", password="")
    short = _register(client, email="short@example.com", password="short")
    oversized = _register(client, email="long@example.com", password="x" * 1_025)

    assert [response.status_code for response in (blank, short, oversized)] == [422, 422, 422]


def test_login_returns_minimal_bearer_token_without_workspace_authority(client):
    assert _register(client).status_code == 201
    response = _login(client)

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["expires_in"] == 1_800
    claims = jwt.decode(response.json()["access_token"], options={"verify_signature": False})
    assert claims["sub"]
    assert not {"workspace_id", "workspace_slug", "role"} & set(claims)


def test_login_failures_are_generic_for_unknown_and_wrong_password(client):
    assert _register(client).status_code == 201

    wrong_password = _login(client, password="wrong-password")
    unknown_user = _login(client, email="unknown@example.com")

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json() == {"detail": "Invalid credentials"}


def test_verified_bearer_resolves_me_and_rejects_missing_or_header_identity(client):
    registered = _register(client)
    token = _login(client).json()["access_token"]

    me = client.get(f"{AUTH_URL}/me", headers=_headers(token))
    assert me.status_code == 200
    assert me.json()["id"] == registered.json()["id"]

    with TestClient(app) as unauthenticated_client:
        missing = unauthenticated_client.get(f"{AUTH_URL}/me")
        forged_header = unauthenticated_client.get(
            f"{AUTH_URL}/me",
            headers={"X-User-Id": registered.json()["id"]},
        )

    assert missing.status_code == forged_header.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"


def test_bad_expired_unknown_and_inactive_tokens_are_all_rejected(client):
    registered = _register(client)
    valid_token = _login(client).json()["access_token"]
    settings = _settings(client)
    now = datetime.now(UTC)
    base_claims = {
        "sub": registered.json()["id"],
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.auth_token_issuer,
    }
    expired = jwt.encode(
        {**base_claims, "exp": now - timedelta(seconds=1)},
        settings.auth_token_secret.get_secret_value(),
        algorithm=settings.auth_token_algorithm,
    )
    unknown = jwt.encode(
        {**base_claims, "sub": str(uuid4())},
        settings.auth_token_secret.get_secret_value(),
        algorithm=settings.auth_token_algorithm,
    )

    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        IdentityMembershipService(session).deactivate_user(UUID(registered.json()["id"]))

    responses = [
        client.get(f"{AUTH_URL}/me", headers=_headers(f"{valid_token}broken")),
        client.get(f"{AUTH_URL}/me", headers=_headers(expired)),
        client.get(f"{AUTH_URL}/me", headers=_headers(unknown)),
        client.get(f"{AUTH_URL}/me", headers=_headers(valid_token)),
    ]
    assert [response.status_code for response in responses] == [401, 401, 401, 401]
    assert {response.json()["detail"] for response in responses} == {"Invalid bearer authentication"}


def test_authenticated_workspace_creation_bootstraps_only_the_creator_as_owner(client):
    registered = _register(client, email="creator@example.com")
    assert registered.status_code == 201
    creator_id = UUID(registered.json()["id"])
    token = _login(client, email="creator@example.com").json()["access_token"]
    response = client.post(
        "/api/workspaces",
        json={"slug": "creator-workspace", "name": "Creator Workspace"},
        headers=_headers(token),
    )

    assert response.status_code == 201
    workspace_id = UUID(response.json()["id"])
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        owner_memberships = list(
            session.exec(
                select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
            )
        )

    assert len(owner_memberships) == 1
    assert owner_memberships[0].user_id == creator_id
    assert owner_memberships[0].role == WorkspaceMemberRole.OWNER


def test_workspace_creation_rejects_unauthenticated_and_client_selected_ownership(client):
    with TestClient(app) as unauthenticated_client:
        unauthenticated = unauthenticated_client.post(
            "/api/workspaces",
            json={"slug": "unauthenticated", "name": "Unauthenticated"},
        )

    assert unauthenticated.status_code == 401

    registered = _register(client, email="owner@example.com")
    token = _login(client, email="owner@example.com").json()["access_token"]
    forged_owner = client.post(
        "/api/workspaces",
        json={
            "slug": "forged-owner",
            "name": "Forged Owner",
            "owner_user_id": str(uuid4()),
            "role": "admin",
        },
        headers=_headers(token),
    )

    assert registered.status_code == 201
    assert forged_owner.status_code == 422


def test_existing_historical_workspace_is_not_assigned_a_fabricated_owner(client):
    session_dependency = app.dependency_overrides[get_session]
    with next(session_dependency()) as session:
        historical = Workspace(slug="historical-workspace", name="Historical Workspace")
        session.add(historical)
        session.commit()
        session.refresh(historical)
        memberships = list(
            session.exec(
                select(WorkspaceMember).where(WorkspaceMember.workspace_id == historical.id)
            )
        )

    assert memberships == []
