from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import (
    approvals_router,
    auth_router,
    conversations_router,
    integrations_router,
    leads_router,
    products_router,
    whatsapp_cloud_router,
    workflows_router,
    workspaces_router,
)
from app.config import get_settings
from app.db import create_db_and_tables

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Multi-agent smart sales agency MVP with human approval.",
    lifespan=lifespan,
)

app.include_router(leads_router, prefix="/api")
app.include_router(products_router, prefix="/api")
app.include_router(workflows_router, prefix="/api")
app.include_router(conversations_router, prefix="/api")
app.include_router(integrations_router, prefix="/api")
app.include_router(whatsapp_cloud_router, prefix="/api")
app.include_router(approvals_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(workspaces_router, prefix="/api")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "mode": settings.llm_mode}
