import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import db
from app.config import Settings
from app.database_resilience import (
    DatabaseStartupRetryPolicy,
    ensure_database_schema_current_with_startup_retry,
)
from app.migration_state import (
    BASELINE_REVISION,
    DatabaseSchemaCheck,
    MigrationSchemaNotCurrentError,
    MigrationSchemaState,
)
from app.models import Workspace


def _schema_check(
    state: MigrationSchemaState,
    *,
    exception_type: str | None = None,
) -> DatabaseSchemaCheck:
    current_revisions = (BASELINE_REVISION,) if state is MigrationSchemaState.CURRENT else ()
    return DatabaseSchemaCheck(
        state=state,
        expected_revision=BASELINE_REVISION,
        current_revisions=current_revisions,
        exception_type=exception_type,
    )


def _structured_logs(caplog) -> str:
    return "\n".join(
        json.dumps(getattr(record, "structured_fields", {}), sort_keys=True)
        for record in caplog.records
    )


def test_transient_startup_schema_check_failure_retries_then_allows_startup(caplog):
    caplog.set_level(logging.INFO, logger="app.database")
    database_url = "postgresql+psycopg://user:do-not-log-this-value@db.example.test/app"
    checks = [
        _schema_check(MigrationSchemaState.CHECK_FAILED, exception_type="OperationalError"),
        _schema_check(MigrationSchemaState.CURRENT),
    ]
    delays: list[float] = []

    def checker(received_url: str) -> DatabaseSchemaCheck:
        assert received_url == database_url
        return checks.pop(0)

    result = ensure_database_schema_current_with_startup_retry(
        database_url,
        DatabaseStartupRetryPolicy(max_attempts=3, retry_delay_seconds=0.01),
        checker=checker,
        sleep=delays.append,
    )

    assert result.is_current
    assert delays == [0.01]
    events = [getattr(record, "event", None) for record in caplog.records]
    assert events == ["database_startup_retry", "database_startup_ready"]
    logs = _structured_logs(caplog)
    assert "OperationalError" in logs
    assert database_url not in logs
    assert "do-not-log-this-value" not in logs


def test_startup_schema_retries_are_bounded_and_final_failure_fails_closed(caplog):
    caplog.set_level(logging.INFO, logger="app.database")
    attempts = 0
    delays: list[float] = []

    def checker(_: str) -> DatabaseSchemaCheck:
        nonlocal attempts
        attempts += 1
        return _schema_check(MigrationSchemaState.CHECK_FAILED, exception_type="OperationalError")

    with pytest.raises(MigrationSchemaNotCurrentError) as exc_info:
        ensure_database_schema_current_with_startup_retry(
            "postgresql+psycopg://user:secret-value@db.example.test/app",
            DatabaseStartupRetryPolicy(max_attempts=2, retry_delay_seconds=0),
            checker=checker,
            sleep=delays.append,
        )

    assert attempts == 2
    assert delays == [0]
    assert "secret-value" not in str(exc_info.value)
    events = [getattr(record, "event", None) for record in caplog.records]
    assert events == ["database_startup_retry", "database_startup_check_failed"]
    assert "secret-value" not in _structured_logs(caplog)


@pytest.mark.parametrize(
    "state",
    [
        MigrationSchemaState.UNINITIALIZED,
        MigrationSchemaState.BEHIND,
        MigrationSchemaState.AHEAD_OR_UNKNOWN,
        MigrationSchemaState.MULTIPLE_HEADS,
    ],
)
def test_known_unsafe_schema_states_fail_immediately_without_retry(state):
    attempts = 0
    delays: list[float] = []

    def checker(_: str) -> DatabaseSchemaCheck:
        nonlocal attempts
        attempts += 1
        return _schema_check(state)

    with pytest.raises(MigrationSchemaNotCurrentError):
        ensure_database_schema_current_with_startup_retry(
            "postgresql+psycopg://user:pw@db.example.test/app",
            DatabaseStartupRetryPolicy(max_attempts=5, retry_delay_seconds=0),
            checker=checker,
            sleep=delays.append,
        )

    assert attempts == 1
    assert delays == []


def test_database_startup_retry_settings_are_bounded():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_startup_max_attempts=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_startup_retry_delay_seconds=-0.1)

    settings = Settings(
        _env_file=None,
        database_startup_max_attempts=2,
        database_startup_retry_delay_seconds=0,
    )

    assert settings.database_startup_max_attempts == 2
    assert settings.database_startup_retry_delay_seconds == 0


def test_postgresql_engine_pool_pre_ping_remains_enabled():
    assert db.engine_kwargs_for_url(
        "postgresql+psycopg://user:pw@db.example.test/app"
    ) == {"pool_pre_ping": True}


def test_request_session_rolls_back_when_dependency_exits_with_error(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db, "engine", test_engine)

    dependency = db.get_session()
    session = next(dependency)
    session.add(Workspace(slug="task299-rollback", name="Task 299 Rollback"))

    with pytest.raises(RuntimeError):
        dependency.throw(RuntimeError("request failed"))

    with Session(test_engine) as verification:
        assert (
            verification.exec(
                select(Workspace).where(Workspace.slug == "task299-rollback")
            ).first()
            is None
        )

    SQLModel.metadata.drop_all(test_engine)
    test_engine.dispose()


def test_task299_does_not_add_business_retry_wrappers():
    root = Path(__file__).resolve().parents[1]
    business_paths = [
        root / "app" / "api" / "routes",
        root / "app" / "services",
        root / "app" / "departments",
    ]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for base in business_paths
        for path in base.rglob("*.py")
    )

    assert "DatabaseStartupRetryPolicy" not in source
    assert "ensure_database_schema_current_with_startup_retry" not in source
    assert "transaction_retry" not in source
    assert "retry_business" not in source
    assert "tenacity" not in source
