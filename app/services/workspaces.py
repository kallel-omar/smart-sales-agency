from sqlmodel import Session, select

from app.config import Settings
from app.models import Workspace


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


def resolve_development_integration_workspace(
    session: Session,
    settings: Settings,
    integration_key: str,
) -> Workspace:
    """
    Resolve a development integration key to an active workspace.

    This deliberately does not accept a workspace identifier from the inbound
    request. A future integration-account resolver can replace this function
    while keeping the API boundary and domain service unchanged.
    """

    if settings.environment not in {"development", "test"}:
        raise InvalidIntegrationContextError(
            "Development integration contexts are not enabled"
        )

    workspace_slug = settings.integration_dev_contexts.get(integration_key)

    if not workspace_slug:
        raise InvalidIntegrationContextError("Integration context is not recognized")

    try:
        return require_active_workspace(session, workspace_slug)
    except (WorkspaceNotFoundError, WorkspaceInactiveError) as exc:
        raise InvalidIntegrationContextError(
            "Integration context is not recognized"
        ) from exc
