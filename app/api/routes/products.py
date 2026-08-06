from fastapi import APIRouter, Query
from sqlmodel import select

from app.api.dependencies import SessionDep
from app.models import Product
from app.schemas import ProductCreate

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=Product, status_code=201)
def create_product(payload: ProductCreate, session: SessionDep) -> Product:
    product = Product(**payload.model_dump())
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


@router.get("", response_model=list[Product])
def list_products(session: SessionDep, tenant_id: str = Query(default="demo")) -> list[Product]:
    statement = select(Product).where(Product.tenant_id == tenant_id, Product.active.is_(True))
    return list(session.exec(statement).all())
