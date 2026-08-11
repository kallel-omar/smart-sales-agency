"""Safe HTTP request observability primitives for FastAPI."""

from __future__ import annotations

import json
import logging
import re
import time
from contextvars import ContextVar, Token
from typing import Any
from uuid import uuid4

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client import generate_latest as generate_prometheus_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

METRICS_PATH = "/metrics"
METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST
REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_HEADER_BYTES = b"x-request-id"
REQUEST_ID_MAX_LENGTH = 100
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
_HTTP_METHOD_PATTERN = re.compile(r"^[A-Z]{1,16}$")
_current_request_id: ContextVar[str | None] = ContextVar(
    "current_http_request_id",
    default=None,
)

http_request_logger = logging.getLogger("app.http")


def generate_request_id() -> str:
    """Return a safe opaque request identifier."""
    return str(uuid4())


def normalize_request_id(value: str | None) -> str | None:
    """Accept only bounded, log-safe request IDs from the trusted header."""
    if value is None:
        return None
    normalized = value.strip()
    if not _REQUEST_ID_PATTERN.fullmatch(normalized):
        return None
    return normalized


def request_id_from_header(value: str | None) -> str:
    """Return a trusted request ID or generate a fresh one."""
    return normalize_request_id(value) or generate_request_id()


def set_current_request_id(request_id: str) -> Token[str | None]:
    return _current_request_id.set(request_id)


def reset_current_request_id(token: Token[str | None]) -> None:
    _current_request_id.reset(token)


def get_current_request_id() -> str | None:
    return _current_request_id.get()


def log_structured_event(
    logger: logging.Logger,
    event: str,
    **fields: Any,
) -> None:
    """Emit one safe structured operational event with request context."""
    request_id = get_current_request_id()
    structured_fields: dict[str, Any] = {"event": event}
    if request_id is not None:
        structured_fields["request_id"] = request_id
    structured_fields.update(fields)

    extra = {
        "structured_fields": structured_fields,
        "event": event,
    }
    for key in ("request_id", "method", "route", "status_code", "duration_ms"):
        if key in structured_fields:
            extra[key] = structured_fields[key]

    try:
        logger.info(event, extra=extra)
    except Exception:  # noqa: BLE001
        # Observability must never alter request behavior.
        return


class JsonLogFormatter(logging.Formatter):
    """JSON formatter that safely serializes structured logging records."""

    def format(self, record: logging.LogRecord) -> str:
        structured_fields = getattr(record, "structured_fields", None)
        if isinstance(structured_fields, dict):
            payload = {
                "level": record.levelname,
                "logger": record.name,
                **{
                    str(key): _safe_json_value(value)
                    for key, value in structured_fields.items()
                },
            }
        else:
            payload = {
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            request_id = getattr(record, "request_id", None)
            if isinstance(request_id, str):
                payload["request_id"] = request_id
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class HttpMetrics:
    """Per-application Prometheus metrics for safe HTTP visibility."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self.requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests processed.",
            ("method", "route", "status_code"),
            registry=self.registry,
        )
        self.request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request duration in seconds.",
            ("method", "route"),
            registry=self.registry,
        )
        self.requests_in_flight = Gauge(
            "http_requests_in_flight",
            "HTTP requests currently in flight.",
            registry=self.registry,
        )

    def increment_in_flight(self) -> None:
        self.requests_in_flight.inc()

    def decrement_in_flight(self) -> None:
        self.requests_in_flight.dec()

    def observe_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        method_label = _safe_method_label(method)
        status_label = str(int(status_code))
        safe_duration = max(duration_seconds, 0.0)
        self.requests_total.labels(method_label, route, status_label).inc()
        self.request_duration_seconds.labels(method_label, route).observe(safe_duration)

    def render_latest(self) -> bytes:
        return generate_prometheus_latest(self.registry)


def configure_logging(*, level: str = "INFO", log_format: str = "json") -> None:
    """Configure standard logging with deterministic local defaults."""
    logging_level = getattr(logging, level.upper(), logging.INFO)
    formatter: logging.Formatter
    if log_format == "json":
        formatter = JsonLogFormatter()
    else:
        formatter = logging.Formatter(
            "%(levelname)s %(name)s %(message)s",
        )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging_level)
    if not root_logger.handlers:
        root_logger.addHandler(logging.StreamHandler())
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)


class RequestObservabilityMiddleware:
    """Attach request IDs, duration measurement, and one completion log."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        metrics: HttpMetrics | None = None,
        metrics_excluded_paths: set[str] | None = None,
    ) -> None:
        self.app = app
        self.metrics = metrics
        self.metrics_excluded_paths = metrics_excluded_paths or {METRICS_PATH}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        should_record_metrics = self._should_record_metrics(scope)
        request_id = request_id_from_header(_request_id_header_from_scope(scope))
        token = set_current_request_id(request_id)
        start = time.perf_counter()
        status_code = 500
        if should_record_metrics:
            _call_metric_safely(self.metrics.increment_in_flight)

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != REQUEST_ID_HEADER_BYTES
                ]
                headers.append((REQUEST_ID_HEADER_BYTES, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_seconds = max(time.perf_counter() - start, 0.0)
            duration_ms = round(duration_seconds * 1000, 3)
            route = safe_route_template(scope)
            log_structured_event(
                http_request_logger,
                "http_request_completed",
                method=str(scope.get("method", "")),
                route=route,
                status_code=status_code,
                duration_ms=max(duration_ms, 0.0),
            )
            if should_record_metrics:
                _call_metric_safely(
                    self.metrics.observe_request,
                    method=str(scope.get("method", "")),
                    route=route,
                    status_code=status_code,
                    duration_seconds=duration_seconds,
                )
                _call_metric_safely(self.metrics.decrement_in_flight)
            reset_current_request_id(token)

    def _should_record_metrics(self, scope: Scope) -> bool:
        if self.metrics is None:
            return False
        return str(scope.get("path", "")) not in self.metrics_excluded_paths


def safe_route_template(scope: Scope) -> str:
    """Return a route template, never a raw URL or query string."""
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path.startswith("/"):
        return _prepend_verified_static_prefix(route_path, str(scope.get("path", "")))
    return "<unmatched>"


def _request_id_header_from_scope(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == REQUEST_ID_HEADER_BYTES:
            try:
                return value.decode("ascii")
            except UnicodeDecodeError:
                return None
    return None


def _safe_method_label(method: str) -> str:
    normalized = method.upper()
    if _HTTP_METHOD_PATTERN.fullmatch(normalized):
        return normalized
    return "OTHER"


def _call_metric_safely(function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return


def _prepend_verified_static_prefix(route_path: str, concrete_path: str) -> str:
    route_segments = [segment for segment in route_path.split("/") if segment]
    concrete_segments = [segment for segment in concrete_path.split("/") if segment]
    if len(concrete_segments) < len(route_segments):
        return route_path

    prefix_length = len(concrete_segments) - len(route_segments)
    for index, route_segment in enumerate(route_segments):
        if route_segment.startswith("{") and route_segment.endswith("}"):
            continue
        if concrete_segments[prefix_length + index] != route_segment:
            return route_path

    prefixed_segments = [*concrete_segments[:prefix_length], *route_segments]
    return "/" + "/".join(prefixed_segments)


def _safe_json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _safe_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_safe_json_value(item) for item in value]
    return str(value)
