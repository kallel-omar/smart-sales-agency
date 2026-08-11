from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

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
from app.observability import (
    METRICS_CONTENT_TYPE,
    METRICS_PATH,
    HttpMetrics,
    RequestObservabilityMiddleware,
    configure_logging,
)
from app.services.rate_limiting import InMemoryFixedWindowRateLimitBackend

settings = get_settings()
configure_logging(level=settings.log_level, log_format=settings.log_format)
http_metrics = HttpMetrics() if settings.metrics_enabled else None
rate_limit_backend = InMemoryFixedWindowRateLimitBackend(
    max_buckets=settings.rate_limit_in_memory_max_buckets,
    cleanup_batch_size=settings.rate_limit_in_memory_cleanup_batch_size,
)


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


if http_metrics is not None:

    @app.get(METRICS_PATH, include_in_schema=False)
    def metrics() -> Response:
        return Response(
            content=http_metrics.render_latest(),
            media_type=METRICS_CONTENT_TYPE,
        )


app.state.http_metrics = http_metrics
app.state.rate_limit_backend = rate_limit_backend
app.add_middleware(RequestObservabilityMiddleware, metrics=http_metrics)
