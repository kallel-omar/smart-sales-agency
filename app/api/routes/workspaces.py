from fastapi import APIRouter, HTTPException
from sqlmodel import select

from app.api.dependencies import AuthenticatedPrincipalDep, CurrentWorkspaceDep, SessionDep
from app.models import Workspace
from app.schemas import (
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceSalesCommunicationRead,
    WorkspaceSalesCommunicationUpdate,
    WorkspaceSalesInstructionsRead,
    WorkspaceSalesInstructionsUpdate,
)
from app.services.workspaces import (
    DuplicateWorkspaceSlugError,
    WorkspaceCreationService,
    WorkspaceSalesCommunicationService,
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


def sales_communication_read(
    workspace: Workspace,
) -> WorkspaceSalesCommunicationRead:
    return WorkspaceSalesCommunicationRead(
        preferred_language=workspace.sales_preferred_language,
        preferred_script=workspace.sales_preferred_script,
        preferred_tone=workspace.sales_preferred_tone,
    )


@router.post("", response_model=WorkspaceRead, status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    session: SessionDep,
    principal: AuthenticatedPrincipalDep,
) -> Workspace:
    slug = payload.slug.strip().lower()
    name = payload.name.strip()

    try:
        return WorkspaceCreationService(session).create_for_principal(
            slug=slug,
            name=name,
            principal=principal,
        )
    except DuplicateWorkspaceSlugError as exc:
        raise HTTPException(
            status_code=409,
            detail="A workspace with this slug already exists",
        ) from exc


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


@router.get(
    "/sales-communication",
    response_model=WorkspaceSalesCommunicationRead,
)
def get_workspace_sales_communication(
    workspace: CurrentWorkspaceDep,
) -> WorkspaceSalesCommunicationRead:
    """Return only the current workspace's trusted Sales communication defaults."""

    return sales_communication_read(workspace)


@router.put(
    "/sales-communication",
    response_model=WorkspaceSalesCommunicationRead,
)
def update_workspace_sales_communication(
    payload: WorkspaceSalesCommunicationUpdate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
) -> WorkspaceSalesCommunicationRead:
    """Update typed defaults selected solely by the current workspace context."""

    updated = WorkspaceSalesCommunicationService(session).update(
        workspace,
        preferred_language=payload.preferred_language,
        preferred_script=payload.preferred_script,
        preferred_tone=payload.preferred_tone,
    )
    return sales_communication_read(updated)


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
