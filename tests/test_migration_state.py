import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings, get_settings
from app.main import create_app
from app.migration_state import (
    BASELINE_REVISION,
    MigrationTopology,
    MigrationSchemaNotCurrentError,
    MigrationSchemaState,
    application_head_revision,
    check_database_schema_state,
    compare_database_schema_to_metadata,
    ensure_database_schema_current,
    main as migration_state_main,
    validate_migration_topology,
)

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"
ALEMBIC_DIR = ROOT / "alembic"
SAFE_PRODUCTION_SECRET = "task298-safe-production-runtime-value"
DEPARTMENT_REVISION = "20260819_001"
CAPABILITY_REVISION = "20260819_002"
AI_EMPLOYEE_REVISION = "20260820_001"
AI_EMPLOYEE_CAPABILITY_ASSIGNMENT_REVISION = "20260820_002"
AI_EMPLOYEE_TOOL_ACCESS_REVISION = "20260820_003"
WORK_ITEM_REVISION = "20260820_004"
WORK_ITEM_APPROVAL_REVISION = "20260820_005"
AI_EXECUTION_ATTRIBUTION_REVISION = "20260820_006"


@pytest.fixture
def postgres_schema_url():
    database_url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required for PostgreSQL migration tests")

    base_url = make_url(database_url)
    schema_name = f"task298_{uuid4().hex}"
    admin_engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    test_url = base_url.set(
        query={**base_url.query, "options": f"-csearch_path={schema_name}"}
    ).render_as_string(hide_password=False)

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    try:
        yield test_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
        get_settings.cache_clear()


def _alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return config


def _upgrade_head(monkeypatch, database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(), "head")
    finally:
        get_settings.cache_clear()


def _production_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="production",
        database_url=database_url,
        auth_token_secret=SAFE_PRODUCTION_SECRET,
        llm_mode="demo",
        outbound_webhook_url="",
        outbound_webhook_signing_enabled=True,
    )


def test_migration_graph_is_single_linear_history_with_task297_baseline_root():
    topology = validate_migration_topology()

    assert topology.root_revision == BASELINE_REVISION
    assert topology.head_revision == application_head_revision()
    assert topology.revisions[0] == BASELINE_REVISION
    assert len(topology.revisions) == len(set(topology.revisions))
    assert topology.revisions == (
        BASELINE_REVISION,
        DEPARTMENT_REVISION,
        CAPABILITY_REVISION,
        AI_EMPLOYEE_REVISION,
        AI_EMPLOYEE_CAPABILITY_ASSIGNMENT_REVISION,
        AI_EMPLOYEE_TOOL_ACCESS_REVISION,
        WORK_ITEM_REVISION,
        WORK_ITEM_APPROVAL_REVISION,
        AI_EXECUTION_ATTRIBUTION_REVISION,
    )


def test_database_check_failure_fails_closed_without_echoing_credentials(monkeypatch):
    secret_url = "postgresql+psycopg://user:do-not-echo-this-value@db.example.test/app"

    def fail_engine(_database_url: str):
        raise RuntimeError("connection failed for password=do-not-echo-this-value")

    monkeypatch.setattr("app.migration_state._create_engine_for_schema_check", fail_engine)

    check = check_database_schema_state(secret_url)
    message = check.safe_operator_message()

    assert check.state is MigrationSchemaState.CHECK_FAILED
    assert check.exception_type == "RuntimeError"
    assert "do-not-echo-this-value" not in message
    assert secret_url not in message


def test_known_ancestor_database_revision_classifies_as_behind(tmp_path, monkeypatch):
    database_path = tmp_path / "behind.sqlite"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": BASELINE_REVISION},
            )
    finally:
        engine.dispose()

    future_head = "20260812_298"
    monkeypatch.setattr(
        "app.migration_state.validate_migration_topology",
        lambda: MigrationTopology(
            root_revision=BASELINE_REVISION,
            head_revision=future_head,
            revisions=(BASELINE_REVISION, future_head),
        ),
    )
    monkeypatch.setattr(
        "app.migration_state._is_ancestor_revision",
        lambda candidate, head: candidate == BASELINE_REVISION and head == future_head,
    )

    check = check_database_schema_state(f"sqlite:///{database_path.as_posix()}")

    assert check.state is MigrationSchemaState.BEHIND
    assert check.expected_revision == future_head
    assert check.current_revisions == (BASELINE_REVISION,)


def test_postgresql_schema_lifecycle_idempotence_and_startup_guard(
    postgres_schema_url,
    monkeypatch,
):
    empty_check = check_database_schema_state(postgres_schema_url)
    assert empty_check.state is MigrationSchemaState.UNINITIALIZED

    with pytest.raises(MigrationSchemaNotCurrentError) as schema_error:
        ensure_database_schema_current(postgres_schema_url)
    assert "postgresql+psycopg" not in str(schema_error.value)
    assert "task298" not in str(schema_error.value)

    calls = {"create_all": 0}

    def fake_create_db_and_tables() -> None:
        calls["create_all"] += 1

    monkeypatch.setattr("app.main.create_db_and_tables", fake_create_db_and_tables)
    uninitialized_app = create_app(_production_settings(postgres_schema_url))
    with pytest.raises(MigrationSchemaNotCurrentError):
        with TestClient(uninitialized_app):
            pass
    assert calls == {"create_all": 0}

    engine = create_engine(postgres_schema_url)
    try:
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    _upgrade_head(monkeypatch, postgres_schema_url)
    _upgrade_head(monkeypatch, postgres_schema_url)

    current_check = check_database_schema_state(postgres_schema_url)
    assert current_check.state is MigrationSchemaState.CURRENT
    assert current_check.current_revisions == (application_head_revision(),)

    ready_app = create_app(_production_settings(postgres_schema_url))
    with TestClient(ready_app) as client:
        assert client.get("/health").status_code == 200
    assert calls == {"create_all": 0}


def test_postgresql_unknown_and_multiple_database_heads_are_rejected(
    postgres_schema_url,
    monkeypatch,
):
    _upgrade_head(monkeypatch, postgres_schema_url)
    future_revision = "20991231_future_revision"

    engine = create_engine(postgres_schema_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :revision"),
                {"revision": future_revision},
            )
        unknown_check = check_database_schema_state(postgres_schema_url)
        assert unknown_check.state is MigrationSchemaState.AHEAD_OR_UNKNOWN
        assert future_revision in unknown_check.safe_operator_message()

        with pytest.raises(MigrationSchemaNotCurrentError):
            ensure_database_schema_current(postgres_schema_url)

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": application_head_revision()},
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": future_revision},
            )
        multiple_check = check_database_schema_state(postgres_schema_url)
        assert multiple_check.state is MigrationSchemaState.MULTIPLE_HEADS
    finally:
        engine.dispose()


def test_migration_precheck_cli_returns_safe_status_for_current_and_unsafe_schema(
    postgres_schema_url,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("DATABASE_URL", postgres_schema_url)
    get_settings.cache_clear()

    try:
        unsafe_exit_code = migration_state_main(["check"])
        unsafe_output = capsys.readouterr().out
        assert unsafe_exit_code == 1
        assert "uninitialized" in unsafe_output
        assert "postgresql+psycopg" not in unsafe_output
        assert "task298" not in unsafe_output

        _upgrade_head(monkeypatch, postgres_schema_url)
        monkeypatch.setenv("DATABASE_URL", postgres_schema_url)
        get_settings.cache_clear()

        current_exit_code = migration_state_main(["check"])
        current_output = capsys.readouterr().out
        assert current_exit_code == 0
        assert "current" in current_output
        assert application_head_revision() in current_output
        assert "postgresql+psycopg" not in current_output
        assert "task298" not in current_output
    finally:
        get_settings.cache_clear()


def test_postgresql_schema_matches_sqlmodel_metadata_after_upgrade(
    postgres_schema_url,
    monkeypatch,
):
    _upgrade_head(monkeypatch, postgres_schema_url)

    diffs = compare_database_schema_to_metadata(postgres_schema_url)

    assert diffs == ()
