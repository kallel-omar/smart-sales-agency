import pytest

from app.db import get_session
from app.main import app
from app.models import Workspace
from app.services.integration_accounts import IntegrationAccountNotFoundError
from app.services.integration_credential_references import (
    IntegrationCredentialPurposeValidationError,
    IntegrationCredentialReferenceNotFoundError,
    IntegrationCredentialReferenceService,
)
from app.services.secret_reference_policy import SecretReferenceValidationError


def create_workspace(slug: str) -> Workspace:
    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        workspace = Workspace(
            slug=slug,
            name=f"{slug} workspace",
        )
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        return workspace


def test_set_reference_creates_workspace_scoped_reference(
    client,
    integration_account_factory,
):
    workspace = create_workspace("credential-ref-create")
    account = integration_account_factory(
        workspace.id,
        "credential-ref-create",
    )

    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        reference = IntegrationCredentialReferenceService(session).set_reference(
            workspace,
            account.id,
            "api_access_token",
            "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
        )

        assert reference.workspace_id == workspace.id
        assert reference.integration_account_id == account.id
        assert reference.purpose == "api_access_token"
        assert (
            reference.secret_reference
            == "INTEGRATION_SECRET_WHATSAPP_API_TOKEN"
        )


def test_set_reference_updates_existing_purpose_instead_of_duplicating(
    client,
    integration_account_factory,
):
    workspace = create_workspace("credential-ref-update")
    account = integration_account_factory(
        workspace.id,
        "credential-ref-update",
    )

    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        service = IntegrationCredentialReferenceService(session)

        original = service.set_reference(
            workspace,
            account.id,
            "api_access_token",
            "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
        )
        original_id = original.id

        updated = service.set_reference(
            workspace,
            account.id,
            "api_access_token",
            "INTEGRATION_SECRET_WHATSAPP_API_TOKEN_ROTATED",
        )

        references = service.list_for_account(workspace, account.id)

        assert updated.id == original_id
        assert (
            updated.secret_reference
            == "INTEGRATION_SECRET_WHATSAPP_API_TOKEN_ROTATED"
        )
        assert len(references) == 1


def test_set_reference_normalizes_purpose(
    client,
    integration_account_factory,
):
    workspace = create_workspace("credential-ref-normalize")
    account = integration_account_factory(
        workspace.id,
        "credential-ref-normalize",
    )

    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        reference = IntegrationCredentialReferenceService(session).set_reference(
            workspace,
            account.id,
            "  API_ACCESS_TOKEN  ",
            "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
        )

        assert reference.purpose == "api_access_token"


def test_list_and_get_references_for_account(
    client,
    integration_account_factory,
):
    workspace = create_workspace("credential-ref-list")
    account = integration_account_factory(
        workspace.id,
        "credential-ref-list",
    )

    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        service = IntegrationCredentialReferenceService(session)

        service.set_reference(
            workspace,
            account.id,
            "webhook_app_secret",
            "INTEGRATION_SECRET_WHATSAPP_APP_SECRET",
        )
        service.set_reference(
            workspace,
            account.id,
            "api_access_token",
            "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
        )

        references = service.list_for_account(workspace, account.id)
        api_token = service.get_for_account(
            workspace,
            account.id,
            "api_access_token",
        )

        assert [reference.purpose for reference in references] == [
            "api_access_token",
            "webhook_app_secret",
        ]
        assert api_token.secret_reference == "INTEGRATION_SECRET_WHATSAPP_API_TOKEN"


def test_reference_access_is_workspace_scoped(
    client,
    integration_account_factory,
):
    owner_workspace = create_workspace("credential-ref-owner")
    other_workspace = create_workspace("credential-ref-other")

    account = integration_account_factory(
        owner_workspace.id,
        "credential-ref-isolation",
    )

    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        service = IntegrationCredentialReferenceService(session)

        service.set_reference(
            owner_workspace,
            account.id,
            "api_access_token",
            "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
        )

        with pytest.raises(IntegrationAccountNotFoundError):
            service.get_for_account(
                other_workspace,
                account.id,
                "api_access_token",
            )

        with pytest.raises(IntegrationAccountNotFoundError):
            service.list_for_account(
                other_workspace,
                account.id,
            )

        with pytest.raises(IntegrationAccountNotFoundError):
            service.set_reference(
                other_workspace,
                account.id,
                "api_access_token",
                "INTEGRATION_SECRET_OTHER_WORKSPACE",
            )


def test_get_missing_reference_raises_not_found(
    client,
    integration_account_factory,
):
    workspace = create_workspace("credential-ref-missing")
    account = integration_account_factory(
        workspace.id,
        "credential-ref-missing",
    )

    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        with pytest.raises(IntegrationCredentialReferenceNotFoundError):
            IntegrationCredentialReferenceService(session).get_for_account(
                workspace,
                account.id,
                "api_access_token",
            )


@pytest.mark.parametrize(
    "purpose",
    [
        "",
        "   ",
        "api-access-token",
        "api access token",
        "_api_access_token",
        "api.access.token",
    ],
)
def test_invalid_purpose_is_rejected(
    client,
    integration_account_factory,
    purpose,
):
    workspace = create_workspace(f"credential-ref-purpose-{abs(hash(purpose))}")
    account = integration_account_factory(
        workspace.id,
        f"credential-ref-purpose-{abs(hash(purpose))}",
    )

    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        with pytest.raises(IntegrationCredentialPurposeValidationError):
            IntegrationCredentialReferenceService(session).set_reference(
                workspace,
                account.id,
                purpose,
                "INTEGRATION_SECRET_WHATSAPP_API_TOKEN",
            )


def test_invalid_secret_reference_is_rejected(
    client,
    integration_account_factory,
):
    workspace = create_workspace("credential-ref-secret-policy")
    account = integration_account_factory(
        workspace.id,
        "credential-ref-secret-policy",
    )

    session_dependency = app.dependency_overrides[get_session]

    with next(session_dependency()) as session:
        with pytest.raises(SecretReferenceValidationError):
            IntegrationCredentialReferenceService(session).set_reference(
                workspace,
                account.id,
                "api_access_token",
                "WHATSAPP_REAL_ACCESS_TOKEN",
            )