from fastapi import APIRouter
from sqlmodel import select

from app.api.dependencies import (
    CurrentWorkspaceDep,
    SalesDataReadPermissionDep,
    SalesDataWritePermissionDep,
    SessionDep,
)
from app.models import Product
from app.schemas import ProductCreate

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=Product, status_code=201)
def create_product(
    payload: ProductCreate,
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: SalesDataWritePermissionDep,
) -> Product:
    product_data = payload.model_dump()
    product_data["tenant_id"] = workspace.slug

    product = Product(**product_data)

    session.add(product)
    session.commit()
    session.refresh(product)

    return product

@router.get("", response_model=list[Product])
def list_products(
    session: SessionDep,
    workspace: CurrentWorkspaceDep,
    _: SalesDataReadPermissionDep,
) -> list[Product]:
    statement = select(Product).where(
        Product.tenant_id == workspace.slug,
        Product.active.is_(True),
    )

    return list(session.exec(statement).all())
