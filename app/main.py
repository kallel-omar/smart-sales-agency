from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    approvals_router,
    auth_router,
    conversations_router,
    integrations_router,
    leads_router,
    meta_router,
    products_router,
    whatsapp_cloud_router,
    workflows_router,
    workspaces_router,
)
from app.config import Settings, get_settings
from app.database_resilience import (
    DatabaseStartupRetryPolicy,
    ensure_database_schema_current_with_startup_retry,
)
from app.db import create_db_and_tables, dispose_engine
from app.error_handling import register_error_handlers
from app.observability import (
    METRICS_CONTENT_TYPE,
    METRICS_PATH,
    HttpMetrics,
    RequestObservabilityMiddleware,
    configure_logging,
)
from app.runtime import ProductionRuntimeValidator
from app.services.rate_limiting import InMemoryFixedWindowRateLimitBackend


def create_app(app_settings: Settings | None = None) -> FastAPI:
    settings = app_settings or get_settings()
    configure_logging(level=settings.log_level, log_format=settings.log_format)
    runtime_policy = ProductionRuntimeValidator(settings).validate()
    http_metrics = HttpMetrics() if settings.metrics_enabled else None
    rate_limit_backend = InMemoryFixedWindowRateLimitBackend(
        max_buckets=settings.rate_limit_in_memory_max_buckets,
        cleanup_batch_size=settings.rate_limit_in_memory_cleanup_batch_size,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        ProductionRuntimeValidator(settings).validate()
        if settings.environment == "production":
            ensure_database_schema_current_with_startup_retry(
                settings.database_url,
                DatabaseStartupRetryPolicy(
                    max_attempts=settings.database_startup_max_attempts,
                    retry_delay_seconds=settings.database_startup_retry_delay_seconds,
                ),
            )
        else:
            create_db_and_tables()
        try:
            yield
        finally:
            dispose_engine()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Multi-agent smart sales agency MVP with human approval.",
        lifespan=lifespan,
        docs_url="/docs" if runtime_policy.api_docs_enabled else None,
        redoc_url="/redoc" if runtime_policy.api_docs_enabled else None,
        openapi_url="/openapi.json" if runtime_policy.api_docs_enabled else None,
    )
    register_error_handlers(app)

    app.include_router(leads_router, prefix="/api")
    app.include_router(meta_router, prefix="/api")
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
    app.state.runtime_policy = runtime_policy
    app.state.settings = settings

    if runtime_policy.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(runtime_policy.cors_allowed_origins),
            allow_credentials=runtime_policy.cors_allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(RequestObservabilityMiddleware, metrics=http_metrics)
    return app


app = create_app()
