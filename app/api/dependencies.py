from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlmodel import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Workspace
from app.services.workspaces import (
    WorkspaceInactiveError,
    WorkspaceNotFoundError,
    require_active_workspace,
)

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

WorkspaceSlugHeader = Annotated[
    str,
    Header(
        alias="X-Workspace-Slug",
        min_length=1,
    ),
]


def get_current_workspace(
    session: SessionDep,
    workspace_slug: WorkspaceSlugHeader,
) -> Workspace:
    try:
        return require_active_workspace(
            session,
            workspace_slug,
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except WorkspaceInactiveError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


CurrentWorkspaceDep = Annotated[
    Workspace,
    Depends(get_current_workspace),
]