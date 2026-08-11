"""Safe FastAPI exception response helpers."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def register_error_handlers(app: FastAPI) -> None:
    """Register focused handlers that preserve existing status semantics."""
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)


async def request_validation_error_handler(
    _: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": sanitized_validation_errors(exc)},
    )


def sanitized_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Preserve FastAPI's error list while removing submitted input values."""
    return jsonable_encoder(
        [_sanitize_validation_error(error) for error in exc.errors()]
    )


def _sanitize_validation_error(error: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in error.items()
        if key not in {"input", "ctx"}
    }
