from sqlmodel import Session, select

from app.models import Workspace


class WorkspaceNotFoundError(LookupError):
    pass


class WorkspaceInactiveError(ValueError):
    pass


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