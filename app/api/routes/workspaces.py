from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.api.dependencies import SessionDep
from app.models import Workspace
from app.schemas import WorkspaceCreate, WorkspaceRead

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


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