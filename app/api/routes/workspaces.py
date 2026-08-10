from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.api.dependencies import CurrentWorkspaceDep, SessionDep
from app.models import Workspace
from app.schemas import (
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceSalesInstructionsRead,
    WorkspaceSalesInstructionsUpdate,
)
from app.services.workspaces import (
    WorkspaceSalesInstructionsService,
    WorkspaceSalesInstructionsValidationError,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def sales_instructions_read(
    workspace: Workspace,
) -> WorkspaceSalesInstructionsRead:
    return WorkspaceSalesInstructionsRead(
        sales_instructions=workspace.sales_instructions,
    )


@router.post("", response_model=WorkspaceRead, status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    session: SessionDep,
) -> Workspace:
    slug = payload.slug.strip().lower()
    name = payload.name.strip()

    existing_workspace = session.exec(
        select(Workspace).where(Workspace.slug == slug)
    ).first()

    if existing_workspace:
        raise HTTPException(
            status_code=409,
            detail="A workspace with this slug already exists",
        )

    workspace = Workspace(
        slug=slug,
        name=name,
    )

    session.add(workspace)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A workspace with this slug already exists",
        ) from exc

    session.refresh(workspace)
    return workspace


@router.get("", response_model=list[WorkspaceRead])
def list_workspaces(session: SessionDep) -> list[Workspace]:
    statement = select(Workspace).order_by(
        Workspace.created_at.desc()
    )

    return list(session.exec(statement).all())


@router.get(
    "/sales-instructions",
    response_model=WorkspaceSalesInstructionsRead,
)
def get_workspace_sales_instructions(
    workspace: CurrentWorkspaceDep,
) -> WorkspaceSalesInstructionsRead:
    """Return only the current workspace's trusted Sales configuration."""

    return sales_instructions_read(workspace)


@router.put(
    "/sales-instructions",
    response_model=WorkspaceSalesInstructionsRead,
)
def replace_workspace_sales_instructions(
    payload: WorkspaceSalesInstructionsUpdate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> WorkspaceSalesInstructionsRead:
    """Replace configuration selected solely by the current workspace context."""

    try:
        updated = WorkspaceSalesInstructionsService(session).replace(
            workspace,
            payload.instructions,
        )
    except WorkspaceSalesInstructionsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return sales_instructions_read(updated)


@router.delete(
    "/sales-instructions",
    response_model=WorkspaceSalesInstructionsRead,
)
def clear_workspace_sales_instructions(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> WorkspaceSalesInstructionsRead:
    """Clear only the current workspace's optional Sales configuration."""

    updated = WorkspaceSalesInstructionsService(session).clear(workspace)
    return sales_instructions_read(updated)


@router.get("/{slug}", response_model=WorkspaceRead)
def get_workspace(
    slug: str,
    session: SessionDep,
) -> Workspace:
    workspace = session.exec(
        select(Workspace).where(
            Workspace.slug == slug.strip().lower()
        )
    ).first()

    if not workspace:
        raise HTTPException(
            status_code=404,
            detail="Workspace not found",
        )

    return workspace
