from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from app.api.dependencies import SessionDep
from app.models import Product
from app.schemas import ProductCreate
from app.services.workspaces import (
    WorkspaceInactiveError,
    WorkspaceNotFoundError,
    require_active_workspace,
)

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=Product, status_code=201)
def create_product(
    payload: ProductCreate,
    session: SessionDep,
) -> Product:
    try:
        workspace = require_active_workspace(
            session,
            payload.tenant_id,
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

    product_data = payload.model_dump()
    product_data["tenant_id"] = workspace.slug

    product = Product(**product_data)

    session.add(product)
    session.commit()
    session.refresh(product)

    return product


@router.get("", response_model=list[Product])
def list_products(session: SessionDep, tenant_id: str = Query(default="demo")) -> list[Product]:
    statement = select(Product).where(Product.tenant_id == tenant_id, Product.active.is_(True))
    return list(session.exec(statement).all())
