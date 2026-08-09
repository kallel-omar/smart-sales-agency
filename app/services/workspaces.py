from dataclasses import dataclass
from hashlib import sha256

from sqlmodel import Session, select

from app.models import IntegrationAccount, Workspace


class WorkspaceNotFoundError(LookupError):
    pass


class WorkspaceInactiveError(ValueError):
    pass


class InvalidIntegrationContextError(PermissionError):
    """Raised when an external integration cannot be mapped safely."""


@dataclass(frozen=True)
class IntegrationContext:
    """The active account and workspace resolved from an inbound credential."""

    account: IntegrationAccount
    workspace: Workspace


def require_active_workspace(
    session: Session,
    slug: str,
) -> Workspace:
    workspace = get_workspace_by_slug(session, slug)
    if not workspace.active:
        raise WorkspaceInactiveError(
            f"Workspace '{workspace.slug}' is inactive"
        )

    return workspace


def get_workspace_by_slug(
    session: Session,
    slug: str,
) -> Workspace:
    """Resolve a workspace without applying an operation-specific active gate."""
    normalized_slug = slug.strip().lower()

    workspace = session.exec(
        select(Workspace).where(
            Workspace.slug == normalized_slug
        )
    ).first()

    if not workspace:
        raise WorkspaceNotFoundError(
            f"Workspace '{normalized_slug}' was not found"
        )
    return workspace


def resolve_integration_account(
    session: Session,
    integration_key: str,
) -> IntegrationAccount:
    """Resolve an inbound credential to its active persisted account."""
    credential_hash = sha256(integration_key.encode()).hexdigest()
    account = session.exec(
        select(IntegrationAccount).where(
            IntegrationAccount.credential_hash == credential_hash,
            IntegrationAccount.active.is_(True),
        )
    ).first()
    if not account:
        raise InvalidIntegrationContextError("Integration context is not recognized")
    return account


def resolve_integration_workspace_for_account(
    session: Session,
    account: IntegrationAccount,
) -> Workspace:
    """Resolve the active workspace only after account authentication succeeds."""
    workspace = session.get(Workspace, account.workspace_id)
    if not workspace or not workspace.active:
        raise InvalidIntegrationContextError(
            "Integration context is not recognized"
        )
    return workspace


def resolve_integration_context(
    session: Session,
    integration_key: str,
) -> IntegrationContext:
    """Resolve an inbound credential to its active account and workspace."""
    account = resolve_integration_account(session, integration_key)
    workspace = resolve_integration_workspace_for_account(session, account)
    return IntegrationContext(account=account, workspace=workspace)


def resolve_integration_workspace(
    session: Session,
    integration_key: str,
) -> Workspace:
    """Compatibility helper for callers that only require the workspace."""
    return resolve_integration_context(session, integration_key).workspace
