import importlib.util
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from alembic import command
from app import db
from app.config import Settings, get_settings
from app.database_urls import database_dialect_name
from app.main import create_app
from app.models import (
    AIInvocationStatus,
    AIInvocationUsage,
    IntegrationAccount,
    Product,
    SQLModel,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)
from app.runtime import ProductionRuntimeValidator, RuntimeConfigurationError

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"
ALEMBIC_DIR = ROOT / "alembic"
BASELINE_REVISION = ALEMBIC_DIR / "versions" / "20260811_297_baseline_current_schema.py"
SAFE_PRODUCTION_SECRET = "task297-safe-production-runtime-value"


def _settings(environment: str = "production", **overrides) -> Settings:
    values = {
        "environment": environment,
        "database_url": "postgresql+psycopg://user:pw@db.example.test/app",
        "auth_token_secret": SAFE_PRODUCTION_SECRET,
        "llm_mode": "demo",
        "outbound_webhook_url": "",
        "outbound_webhook_signing_enabled": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return config


def test_database_url_policy_accepts_sqlite_for_development_and_postgresql_for_production():
    sqlite_settings = _settings(
        "development",
        database_url="sqlite:///./task297_dev.db",
        auth_token_secret="",
        outbound_webhook_signing_enabled=False,
    )
    postgresql_settings = _settings("production")

    assert database_dialect_name(sqlite_settings.database_url) == "sqlite"
    assert database_dialect_name(postgresql_settings.database_url) == "postgresql"
    ProductionRuntimeValidator(sqlite_settings).validate()
    ProductionRuntimeValidator(postgresql_settings).validate()


def test_production_rejects_sqlite_and_does_not_echo_database_url_or_credentials():
    credential_url = "mysql+pymysql://user:do-not-echo-this-value@db.example.test/app"

    with pytest.raises(RuntimeConfigurationError) as sqlite_error:
        ProductionRuntimeValidator(
            _settings("production", database_url="sqlite:///./do-not-use-prod.db")
        ).validate()
    with pytest.raises(RuntimeConfigurationError) as credential_error:
        ProductionRuntimeValidator(_settings("production", database_url=credential_url)).validate()

    assert "DATABASE_URL" in str(sqlite_error.value)
    assert "sqlite:///./do-not-use-prod.db" not in str(sqlite_error.value)
    assert "do-not-echo-this-value" not in str(credential_error.value)
    assert credential_url not in str(credential_error.value)


def test_engine_factory_keeps_sqlite_options_out_of_postgresql(monkeypatch):
    calls: list[dict] = []

    def fake_create_engine(database_url: str, **kwargs):
        calls.append({"database_url": database_url, **kwargs})
        return object()

    monkeypatch.setattr(db, "create_engine", fake_create_engine)

    db.create_app_engine("sqlite:///./local.db")
    db.create_app_engine("postgresql+psycopg://user:pw@db.example.test/app")

    assert calls[0]["connect_args"] == {"check_same_thread": False}
    assert "pool_pre_ping" not in calls[0]
    assert calls[1]["pool_pre_ping"] is True
    assert "connect_args" not in calls[1]


def test_alembic_configuration_uses_runtime_settings_and_application_metadata():
    alembic_ini = ALEMBIC_INI.read_text(encoding="utf-8")
    env_py = (ALEMBIC_DIR / "env.py").read_text(encoding="utf-8")

    assert "sqlite:///__runtime_database_url_is_loaded_from_app_settings__" in alembic_ini
    assert "get_settings().database_url" in env_py
    assert "target_metadata = SQLModel.metadata" in env_py
    assert "disable_existing_loggers=False" in env_py
    assert "postgresql+psycopg://" not in alembic_ini
    assert "do-not-echo" not in alembic_ini + env_py


def test_baseline_migration_exists_and_imports_cleanly():
    assert BASELINE_REVISION.exists()
    migration_text = BASELINE_REVISION.read_text(encoding="utf-8")
    assert 'revision: str = "20260811_297"' in migration_text
    assert "SQLModel.metadata.create_all" in migration_text
    assert "SQLModel.metadata.drop_all" in migration_text

    spec = importlib.util.spec_from_file_location("task297_baseline", BASELINE_REVISION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "20260811_297"
    assert module.down_revision is None


def test_sqlite_migration_to_head_matches_application_metadata(tmp_path, monkeypatch):
    database_path = tmp_path / "task297.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()

    try:
        command.upgrade(_alembic_config(), "head")
        engine = create_engine(f"sqlite:///{database_path.as_posix()}")
        try:
            actual_tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()

    assert set(SQLModel.metadata.tables) | {"alembic_version"} <= actual_tables


def test_provider_auth_mode_migration_backfills_only_existing_instagram_accounts(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "task291-auth-mode.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    try:
        command.upgrade(_alembic_config(), "head")
        command.downgrade(_alembic_config(), "20260820_011")
        engine = create_engine(database_url)
        workspace_id = uuid4()
        instagram_account_id = uuid4()
        messenger_account_id = uuid4()
        now = datetime.now(UTC).replace(tzinfo=None)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workspace "
                        "(id, slug, name, active, ai_model_tier_downgrade_mappings, "
                        "created_at, updated_at) "
                        "VALUES (:id, :slug, :name, :active, :mappings, :created_at, :updated_at)"
                    ),
                    {
                        "id": workspace_id.hex,
                        "slug": "task291-migration",
                        "name": "Task 291 Migration",
                        "active": True,
                        "mappings": "{}",
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                for account_id, provider, external_account_id, credential_hash in (
                    (instagram_account_id, "instagram_dm", "ig-legacy", "a" * 64),
                    (messenger_account_id, "facebook_messenger", "page-legacy", "b" * 64),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO integrationaccount "
                            "(id, workspace_id, provider, external_account_id, secret_reference, "
                            "credential_hash, active, created_at, updated_at) "
                            "VALUES (:id, :workspace_id, :provider, :external_account_id, NULL, "
                            ":credential_hash, :active, :created_at, :updated_at)"
                        ),
                        {
                            "id": account_id.hex,
                            "workspace_id": workspace_id.hex,
                            "provider": provider,
                            "external_account_id": external_account_id,
                            "credential_hash": credential_hash,
                            "active": True,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
        finally:
            engine.dispose()

        command.upgrade(_alembic_config(), "head")
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT provider, provider_auth_mode FROM integrationaccount "
                        "ORDER BY provider"
                    )
                ).all()
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()

    assert rows == [
        ("facebook_messenger", None),
        ("instagram_dm", "facebook_login"),
    ]


def test_tiktok_migration_enforces_only_one_active_owner_and_preserves_history(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "task293-tiktok.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    try:
        command.upgrade(_alembic_config(), "head")
        engine = create_engine(database_url)
        workspace_id = uuid4()
        now = datetime.now(UTC).replace(tzinfo=None)
        try:
            inspector = inspect(engine)
            columns = {
                column["name"]
                for column in inspector.get_columns("integrationaccount")
            }
            indexes = {
                index["name"]: index
                for index in inspector.get_indexes("integrationaccount")
            }
            assert "comment_to_message_eligible" in columns
            assert indexes["uq_active_tiktok_dm_external_account"]["unique"] == 1

            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workspace "
                        "(id, slug, name, active, ai_model_tier_downgrade_mappings, "
                        "created_at, updated_at) "
                        "VALUES (:id, :slug, :name, :active, :mappings, "
                        ":created_at, :updated_at)"
                    ),
                    {
                        "id": workspace_id.hex,
                        "slug": "task293-migration",
                        "name": "Task 293 Migration",
                        "active": True,
                        "mappings": "{}",
                        "created_at": now,
                        "updated_at": now,
                    },
                )

            def insert_account(provider, external_id, active, credential_hash):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO integrationaccount "
                            "(id, workspace_id, provider, external_account_id, "
                            "provider_auth_mode, comment_to_message_eligible, "
                            "secret_reference, credential_hash, active, created_at, updated_at) "
                            "VALUES (:id, :workspace_id, :provider, :external_account_id, "
                            "NULL, :eligible, NULL, :credential_hash, :active, "
                            ":created_at, :updated_at)"
                        ),
                        {
                            "id": uuid4().hex,
                            "workspace_id": workspace_id.hex,
                            "provider": provider,
                            "external_account_id": external_id,
                            "eligible": False,
                            "credential_hash": credential_hash,
                            "active": active,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )

            insert_account("tiktok_dm", "shared-business", True, "a" * 64)
            insert_account("tiktok_dm", "shared-business", False, "b" * 64)
            insert_account("tiktok_dm", "shared-business", False, "c" * 64)
            insert_account("facebook_messenger", "shared-business", True, "d" * 64)
            with pytest.raises(IntegrityError):
                insert_account("tiktok_dm", "shared-business", True, "e" * 64)

            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT provider, active, comment_to_message_eligible "
                        "FROM integrationaccount ORDER BY credential_hash"
                    )
                ).all()
            assert rows == [
                ("tiktok_dm", 1, 0),
                ("tiktok_dm", 0, 0),
                ("tiktok_dm", 0, 0),
                ("facebook_messenger", 1, 0),
            ]
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def test_channel_connection_migration_backfills_status_preserves_active_and_downgrades(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "task295b-lifecycle.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    try:
        command.upgrade(_alembic_config(), "head")
        command.downgrade(_alembic_config(), "20260825_013")
        engine = create_engine(database_url)
        workspace_id = uuid4()
        now = datetime.now(UTC).replace(tzinfo=None)
        account_ids = (uuid4(), uuid4())
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workspace "
                        "(id, slug, name, active, ai_model_tier_downgrade_mappings, "
                        "created_at, updated_at) VALUES "
                        "(:id, :slug, :name, :active, :mappings, :created_at, :updated_at)"
                    ),
                    {
                        "id": workspace_id.hex,
                        "slug": "task295b-migration",
                        "name": "Task 295B Migration",
                        "active": True,
                        "mappings": "{}",
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                for account_id, active, credential_hash in (
                    (account_ids[0], True, "a" * 64),
                    (account_ids[1], False, "b" * 64),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO integrationaccount "
                            "(id, workspace_id, provider, external_account_id, "
                            "provider_auth_mode, comment_to_message_eligible, "
                            "secret_reference, credential_hash, active, created_at, updated_at) "
                            "VALUES (:id, :workspace_id, 'generic_hmac', :external_id, "
                            "NULL, 0, NULL, :credential_hash, :active, :created_at, :updated_at)"
                        ),
                        {
                            "id": account_id.hex,
                            "workspace_id": workspace_id.hex,
                            "external_id": f"legacy-{credential_hash[0]}",
                            "credential_hash": credential_hash,
                            "active": active,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
        finally:
            engine.dispose()

        command.upgrade(_alembic_config(), "head")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            account_columns = {
                column["name"]
                for column in inspector.get_columns("integrationaccount")
            }
            credential_columns = {
                column["name"]
                for column in inspector.get_columns("integrationcredentialreference")
            }
            audit_columns = {
                column["name"]
                for column in inspector.get_columns("integrationaccountauditevent")
            }
            assert {
                "connection_status",
                "last_validated_at",
                "reconnect_required_at",
                "last_connection_error_code",
            } <= account_columns
            assert "expires_at" in credential_columns
            assert {"actor_user_id", "credential_purpose", "reason_code"} <= audit_columns
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT active, connection_status FROM integrationaccount "
                        "ORDER BY credential_hash"
                    )
                ).all()
            assert rows == [(1, "configured"), (0, "configured")]
        finally:
            engine.dispose()

        command.downgrade(_alembic_config(), "20260825_013")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            assert "connection_status" not in {
                column["name"]
                for column in inspector.get_columns("integrationaccount")
            }
            assert "expires_at" not in {
                column["name"]
                for column in inspector.get_columns("integrationcredentialreference")
            }
            assert "actor_user_id" not in {
                column["name"]
                for column in inspector.get_columns("integrationaccountauditevent")
            }
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def test_channel_connection_migration_rejects_ambiguous_active_provider_ownership(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "task295b-conflict.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    try:
        command.upgrade(_alembic_config(), "head")
        command.downgrade(_alembic_config(), "20260825_013")
        engine = create_engine(database_url)
        workspace_id = uuid4()
        now = datetime.now(UTC).replace(tzinfo=None)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workspace "
                        "(id, slug, name, active, ai_model_tier_downgrade_mappings, "
                        "created_at, updated_at) VALUES "
                        "(:id, :slug, :name, 1, '{}', :created_at, :updated_at)"
                    ),
                    {
                        "id": workspace_id.hex,
                        "slug": "task295b-conflict",
                        "name": "Task 295B Conflict",
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                for marker in ("a", "b"):
                    connection.execute(
                        text(
                            "INSERT INTO integrationaccount "
                            "(id, workspace_id, provider, external_account_id, "
                            "provider_auth_mode, comment_to_message_eligible, "
                            "secret_reference, credential_hash, active, created_at, updated_at) "
                            "VALUES (:id, :workspace_id, 'whatsapp_cloud', 'duplicate-phone', "
                            "NULL, 0, NULL, :credential_hash, 1, :created_at, :updated_at)"
                        ),
                        {
                            "id": uuid4().hex,
                            "workspace_id": workspace_id.hex,
                            "credential_hash": marker * 64,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
        finally:
            engine.dispose()

        with pytest.raises(
            RuntimeError,
            match="Active provider identity ownership conflicts must be resolved",
        ):
            command.upgrade(_alembic_config(), "head")
    finally:
        get_settings.cache_clear()


def test_production_startup_does_not_run_create_all(monkeypatch):
    calls = {"create_all": 0}

    def fake_create_db_and_tables() -> None:
        calls["create_all"] += 1

    monkeypatch.setattr("app.main.create_db_and_tables", fake_create_db_and_tables)
    monkeypatch.setattr(
        "app.main.ensure_database_schema_current_with_startup_retry",
        lambda database_url, policy: None,
    )
    local_app = create_app(_settings("production"))

    with TestClient(local_app) as client:
        assert client.get("/health").status_code == 200

    assert calls == {"create_all": 0}


def test_development_startup_still_supports_create_all(monkeypatch):
    calls = {"create_all": 0}

    def fake_create_db_and_tables() -> None:
        calls["create_all"] += 1

    monkeypatch.setattr("app.main.create_db_and_tables", fake_create_db_and_tables)
    local_app = create_app(
        _settings(
            "development",
            database_url="sqlite://",
            auth_token_secret="",
            outbound_webhook_signing_enabled=False,
        )
    )

    with TestClient(local_app) as client:
        assert client.get("/health").status_code == 200

    assert calls == {"create_all": 1}


def test_postgresql_migration_and_representative_schema_roundtrip(monkeypatch):
    database_url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required for the PostgreSQL smoke test")

    base_url = make_url(database_url)
    schema_name = f"task297_{uuid4().hex}"
    admin_engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    test_url = base_url.set(
        query={**base_url.query, "options": f"-csearch_path={schema_name}"}
    ).render_as_string(hide_password=False)

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    monkeypatch.setenv("DATABASE_URL", test_url)
    get_settings.cache_clear()

    try:
        command.upgrade(_alembic_config(), "head")
        engine = db.create_app_engine(test_url)
        try:
            table_names = set(inspect(engine).get_table_names())
            assert set(SQLModel.metadata.tables) | {"alembic_version"} <= table_names

            with Session(engine) as session:
                workspace = Workspace(
                    slug=f"task297-{uuid4().hex}",
                    name="Task 297 Workspace",
                    ai_estimated_spend_limit=Decimal("12.34000000"),
                    ai_model_tier_downgrade_mappings={"premium": "standard"},
                )
                user = User(
                    email=f"task297-{uuid4().hex}@example.test",
                    display_name="Task 297 Operator",
                )
                session.add(workspace)
                session.add(user)
                session.commit()
                session.refresh(workspace)
                session.refresh(user)

                membership = WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role=WorkspaceMemberRole.OWNER,
                )
                product = Product(
                    tenant_id=workspace.slug,
                    name="Task 297 Product",
                    description="Portable PostgreSQL compatibility product.",
                    price=19.95,
                    metadata_json={"channels": ["whatsapp_cloud"], "nested": {"ok": True}},
                )
                usage = AIInvocationUsage(
                    workspace_id=workspace.id,
                    task_identifier="task297",
                    agent_identifier="postgres-smoke",
                    provider="demo",
                    model="demo",
                    latency_ms=1,
                    estimated_cost=Decimal("0.12345678"),
                    status=AIInvocationStatus.SUCCESSFUL,
                )
                session.add(membership)
                session.add(product)
                session.add(usage)
                session.commit()

                stored_product = session.exec(
                    select(Product).where(Product.id == product.id)
                ).one()
                stored_usage = session.exec(
                    select(AIInvocationUsage).where(AIInvocationUsage.id == usage.id)
                ).one()
                assert stored_product.metadata_json["nested"]["ok"] is True
                assert stored_product.id == product.id
                assert stored_usage.estimated_cost == Decimal("0.12345678")

                duplicate_workspace = Workspace(
                    slug=workspace.slug,
                    name="Duplicate Workspace",
                    ai_model_tier_downgrade_mappings={},
                )
                session.add(duplicate_workspace)
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()

                invalid_account = IntegrationAccount(
                    workspace_id=uuid4(),
                    provider="generic_hmac",
                    credential_hash=uuid4().hex + uuid4().hex,
                )
                session.add(invalid_account)
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
