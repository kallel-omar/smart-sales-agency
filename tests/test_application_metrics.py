import asyncio
import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.observability import (
    METRICS_CONTENT_TYPE,
    METRICS_PATH,
    REQUEST_ID_HEADER,
    HttpMetrics,
    RequestObservabilityMiddleware,
)


def _build_metrics_app(
    *,
    metrics_enabled: bool = True,
    metrics: Any | None = None,
) -> tuple[FastAPI, Any | None]:
    app = FastAPI()
    http_metrics = metrics if metrics is not None else (HttpMetrics() if metrics_enabled else None)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/missing/{item_id}")
    def missing(item_id: str) -> dict[str, str]:
        raise HTTPException(status_code=404, detail=f"missing {item_id}")

    @app.get("/boom/{item_id}")
    def boom(item_id: str) -> dict[str, str]:
        raise RuntimeError(f"unexpected {item_id}")

    @app.get("/accounts/{account_id}/actions/{action_id}")
    def scoped_action(account_id: str, action_id: str) -> dict[str, str]:
        return {"account_id": account_id, "action_id": action_id}

    if http_metrics is not None and hasattr(http_metrics, "render_latest"):

        @app.get(METRICS_PATH, include_in_schema=False)
        def metrics_endpoint() -> Response:
            return Response(
                content=http_metrics.render_latest(),
                media_type=METRICS_CONTENT_TYPE,
            )

    app.add_middleware(RequestObservabilityMiddleware, metrics=http_metrics)
    return app, http_metrics


def _metrics_text(client: TestClient) -> str:
    response = client.get(METRICS_PATH)
    assert response.status_code == 200
    assert response.headers["content-type"] == METRICS_CONTENT_TYPE
    return response.text


def _sample_value(
    metrics_text: str,
    sample_name: str,
    labels: dict[str, str] | None = None,
) -> float | None:
    expected_labels = labels or {}
    for family in text_string_to_metric_families(metrics_text):
        for sample in family.samples:
            if sample.name == sample_name and dict(sample.labels) == expected_labels:
                return float(sample.value)
    return None


def test_metrics_endpoint_exports_prometheus_http_metrics_without_counting_itself():
    app, _ = _build_metrics_app()

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        first_metrics = _metrics_text(client)
        second_metrics = _metrics_text(client)

    assert "# HELP http_requests_total" in first_metrics
    assert "# HELP http_request_duration_seconds" in first_metrics
    assert "# HELP http_requests_in_flight" in first_metrics
    assert list(text_string_to_metric_families(first_metrics))
    assert 'route="/metrics"' not in first_metrics
    assert 'route="/metrics"' not in second_metrics
    assert _sample_value(
        second_metrics,
        "http_requests_total",
        {"method": "GET", "route": "/health", "status_code": "200"},
    ) == 1.0


def test_metrics_can_be_disabled_and_metrics_route_is_absent():
    app, _ = _build_metrics_app(metrics_enabled=False)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.get(METRICS_PATH)

    assert response.status_code == 404


def test_metrics_record_safe_status_codes_and_duration_histogram():
    app, _ = _build_metrics_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/missing/task292-sensitive-missing").status_code == 404
        assert client.get("/boom/task292-sensitive-boom").status_code == 500
        metrics_text = _metrics_text(client)

    assert _sample_value(
        metrics_text,
        "http_requests_total",
        {"method": "GET", "route": "/health", "status_code": "200"},
    ) == 1.0
    assert _sample_value(
        metrics_text,
        "http_requests_total",
        {"method": "GET", "route": "/missing/{item_id}", "status_code": "404"},
    ) == 1.0
    assert _sample_value(
        metrics_text,
        "http_requests_total",
        {"method": "GET", "route": "/boom/{item_id}", "status_code": "500"},
    ) == 1.0
    assert _sample_value(
        metrics_text,
        "http_request_duration_seconds_count",
        {"method": "GET", "route": "/health"},
    ) == 1.0
    assert "task292-sensitive-missing" not in metrics_text
    assert "task292-sensitive-boom" not in metrics_text


def test_metrics_labels_use_route_templates_not_ids_queries_or_request_context():
    app, _ = _build_metrics_app()

    with TestClient(app) as client:
        response = client.get(
            (
                "/accounts/account-task292-secret/actions/action-task292-secret"
                "?customer_message=leak-task292-query"
            ),
            headers={
                REQUEST_ID_HEADER: "task292-request-secret",
                "Authorization": "Bearer task292-auth-secret",
            },
        )
        assert response.status_code == 200
        metrics_text = _metrics_text(client)

    assert _sample_value(
        metrics_text,
        "http_requests_total",
        {
            "method": "GET",
            "route": "/accounts/{account_id}/actions/{action_id}",
            "status_code": "200",
        },
    ) == 1.0
    for forbidden in (
        "account-task292-secret",
        "action-task292-secret",
        "leak-task292-query",
        "task292-request-secret",
        "task292-auth-secret",
        "customer_message",
    ):
        assert forbidden not in metrics_text


@pytest.mark.asyncio
async def test_in_flight_gauge_reflects_concurrent_requests_and_returns_to_zero():
    app = FastAPI()
    http_metrics = HttpMetrics()
    request_started = asyncio.Event()
    release_request = asyncio.Event()

    @app.get("/blocked")
    async def blocked() -> dict[str, bool]:
        request_started.set()
        await release_request.wait()
        return {"ok": True}

    @app.get(METRICS_PATH, include_in_schema=False)
    async def metrics_endpoint() -> Response:
        return Response(
            content=http_metrics.render_latest(),
            media_type=METRICS_CONTENT_TYPE,
        )

    app.add_middleware(RequestObservabilityMiddleware, metrics=http_metrics)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        blocked_task = asyncio.create_task(client.get("/blocked"))
        await asyncio.wait_for(request_started.wait(), timeout=1)

        metrics_during_request = (await client.get(METRICS_PATH)).text
        assert _sample_value(metrics_during_request, "http_requests_in_flight") == 1.0

        release_request.set()
        response = await blocked_task
        assert response.status_code == 200

        metrics_after_request = (await client.get(METRICS_PATH)).text
        assert _sample_value(metrics_after_request, "http_requests_in_flight") == 0.0
        assert _sample_value(
            metrics_after_request,
            "http_requests_total",
            {"method": "GET", "route": "/blocked", "status_code": "200"},
        ) == 1.0


def test_in_flight_gauge_is_cleared_after_unexpected_exception():
    app, _ = _build_metrics_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom/task292-sensitive-failure").status_code == 500
        metrics_text = _metrics_text(client)

    assert _sample_value(metrics_text, "http_requests_in_flight") == 0.0
    assert _sample_value(
        metrics_text,
        "http_requests_total",
        {"method": "GET", "route": "/boom/{item_id}", "status_code": "500"},
    ) == 1.0
    assert "task292-sensitive-failure" not in metrics_text


def test_metric_failures_do_not_break_http_requests():
    class FailingMetrics:
        def increment_in_flight(self) -> None:
            raise RuntimeError("metric start failed")

        def decrement_in_flight(self) -> None:
            raise RuntimeError("metric end failed")

        def observe_request(self, **_: Any) -> None:
            raise RuntimeError("metric observe failed")

    app, _ = _build_metrics_app(metrics=FailingMetrics())

    with TestClient(app) as client:
        response = client.get("/health", headers={REQUEST_ID_HEADER: "task292-safe"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "task292-safe"


def test_metrics_registry_is_isolated_per_app_instance():
    first_app, _ = _build_metrics_app()
    second_app, _ = _build_metrics_app()

    with TestClient(first_app) as first_client:
        assert first_client.get("/health").status_code == 200
        first_metrics = _metrics_text(first_client)
    with TestClient(second_app) as second_client:
        second_metrics = _metrics_text(second_client)

    assert _sample_value(
        first_metrics,
        "http_requests_total",
        {"method": "GET", "route": "/health", "status_code": "200"},
    ) == 1.0
    assert (
        _sample_value(
            second_metrics,
            "http_requests_total",
            {"method": "GET", "route": "/health", "status_code": "200"},
        )
        is None
    )


def test_metrics_preserve_task291_request_completion_logging(caplog):
    app, _ = _build_metrics_app()
    caplog.set_level(logging.INFO, logger="app.http")

    with TestClient(app) as client:
        response = client.get("/health", headers={REQUEST_ID_HEADER: "task292-log"})

    completion_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http_request_completed"
    ]
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "task292-log"
    assert len(completion_records) == 1
    payload = completion_records[0].structured_fields
    assert payload["request_id"] == "task292-log"
    assert payload["route"] == "/health"
    assert payload["status_code"] == 200
