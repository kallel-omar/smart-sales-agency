"""Central database startup resilience helpers.

This module is deliberately limited to startup checks and connection/session
cleanup support. It must not grow into a transparent retry layer for business
mutations.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.migration_state import (
    DatabaseSchemaCheck,
    MigrationSchemaNotCurrentError,
    MigrationSchemaState,
    check_database_schema_state,
)
from app.observability import log_structured_event

database_resilience_logger = logging.getLogger("app.database")
_SAFE_EXCEPTION_TYPE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,100}$")


@dataclass(frozen=True)
class DatabaseStartupRetryPolicy:
    max_attempts: int = 3
    retry_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("database startup max attempts must be at least 1")
        if self.retry_delay_seconds < 0:
            raise ValueError("database startup retry delay must not be negative")


DatabaseSchemaChecker = Callable[[str], DatabaseSchemaCheck]
SleepFunction = Callable[[float], None]


def ensure_database_schema_current_with_startup_retry(
    database_url: str,
    policy: DatabaseStartupRetryPolicy,
    *,
    checker: DatabaseSchemaChecker = check_database_schema_state,
    sleep: SleepFunction = time.sleep,
) -> DatabaseSchemaCheck:
    """Ensure production schema state is current with bounded transient retry.

    Only ``CHECK_FAILED`` is retried because it represents a failed inspection
    such as a temporarily unavailable PostgreSQL server. Known schema states
    are deterministic operator problems and fail immediately.
    """

    max_attempts = max(1, policy.max_attempts)
    for attempt in range(1, max_attempts + 1):
        check = checker(database_url)
        if check.is_current:
            log_structured_event(
                database_resilience_logger,
                "database_startup_ready",
                attempt=attempt,
                max_attempts=max_attempts,
                state=check.state.value,
            )
            return check

        if check.state is MigrationSchemaState.CHECK_FAILED and attempt < max_attempts:
            log_structured_event(
                database_resilience_logger,
                "database_startup_retry",
                attempt=attempt,
                max_attempts=max_attempts,
                delay_seconds=policy.retry_delay_seconds,
                state=check.state.value,
                exception_type=_safe_exception_type(check.exception_type),
            )
            sleep(policy.retry_delay_seconds)
            continue

        log_structured_event(
            database_resilience_logger,
            "database_startup_check_failed",
            attempt=attempt,
            max_attempts=max_attempts,
            state=check.state.value,
            exception_type=_safe_exception_type(check.exception_type),
        )
        raise MigrationSchemaNotCurrentError(check.safe_operator_message())

    raise MigrationSchemaNotCurrentError("Database schema check failed safely")


def _safe_exception_type(value: str | None) -> str:
    if value is None:
        return "none"
    if _SAFE_EXCEPTION_TYPE_PATTERN.fullmatch(value):
        return value
    return "unsafe_exception_type"
