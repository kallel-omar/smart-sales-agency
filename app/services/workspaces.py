from sqlmodel import Session, select

from hashlib import sha256

from app.models import IntegrationAccount, Workspace


class WorkspaceNotFoundError(LookupError):
    pass


class WorkspaceInactiveError(ValueError):
    pass


class InvalidIntegrationContextError(PermissionError):
    """Raised when an external integration cannot be mapped safely."""


def require_active_workspace(
    session: Session,
    slug: str,
) -> Workspace:
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

    if not workspace.active:
        raise WorkspaceInactiveError(
            f"Workspace '{normalized_slug}' is inactive"
        )

    return workspace


def resolve_integration_workspace(
    session: Session,
    integration_key: str,
) -> Workspace:
    """Resolve an inbound credential to its active, persisted workspace."""
    credential_hash = sha256(integration_key.encode()).hexdigest()
    account = session.exec(
        select(IntegrationAccount).where(
            IntegrationAccount.credential_hash == credential_hash,
            IntegrationAccount.active.is_(True),
        )
    ).first()
    if not account:
        raise InvalidIntegrationContextError("Integration context is not recognized")
    workspace = session.get(Workspace, account.workspace_id)
    if not workspace or not workspace.active:
        raise InvalidIntegrationContextError(
            "Integration context is not recognized"
        )
    return workspace
