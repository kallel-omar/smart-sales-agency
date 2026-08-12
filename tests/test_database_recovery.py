import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from alembic import command
from app import db
from app.config import Settings, get_settings
from app.database_backup import (
    BackupManifestError,
    create_backup_manifest,
    verify_backup_manifest,
    write_backup_manifest,
)
from app.main import create_app
from app.migration_state import (
    MigrationSchemaState,
    application_head_revision,
    check_database_schema_state,
)
from app.models import (
    AIInvocationStatus,
    AIInvocationUsage,
    ConversationMessage,
    IntegrationAccount,
    Lead,
    Product,
    SalesStage,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberRole,
)

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"
ALEMBIC_DIR = ROOT / "alembic"
SAFE_PRODUCTION_SECRET = "task299-safe-production-runtime-value"
_SAFE_DATABASE_NAME = re.compile(r"^[a-z0-9_]{1,63}$")


@dataclass(frozen=True)
class PostgresServer:
    url: str
    container_name: str | None = None


@dataclass(frozen=True)
class RecoveryDataset:
    workspace_id: UUID
    workspace_slug: str
    user_id: UUID
    lead_id: UUID
    product_id: UUID
    message_id: UUID
    usage_id: UUID


@pytest.fixture
def postgres_server():
    configured_url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if configured_url:
        yield PostgresServer(configured_url)
        return

    if shutil.which("docker") is None:
        pytest.skip("Docker is required to start disposable PostgreSQL locally")

    port = _free_tcp_port()
    container_name = f"task299-postgres-{uuid4().hex}"
    password = "task299-test-password"
    run_result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-e",
            "POSTGRES_USER=task299",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            "POSTGRES_DB=task299",
            "-p",
            f"127.0.0.1:{port}:5432",
            "postgres:16-alpine",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    if run_result.returncode != 0:
        pytest.skip("Disposable PostgreSQL container could not start")

    database_url = f"postgresql+psycopg://task299:{password}@127.0.0.1:{port}/task299"
    try:
        _wait_for_postgres(database_url)
        yield PostgresServer(database_url, container_name=container_name)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )


def test_backup_manifest_verifies_archive_integrity_without_secret_metadata(tmp_path):
    archive = tmp_path / "task299.dump"
    archive.write_bytes(b"synthetic-postgresql-custom-archive")

    manifest = create_backup_manifest(
        archive,
        application_revision=application_head_revision(),
    )
    manifest_path = tmp_path / "task299.dump.manifest.json"
    write_backup_manifest(manifest, manifest_path)

    verify_backup_manifest(manifest, backup_directory=tmp_path)
    serialized = manifest_path.read_text(encoding="utf-8")
    assert manifest.archive_filename == archive.name
    assert manifest.archive_size_bytes == archive.stat().st_size
    assert "postgresql+psycopg://" not in serialized
    assert "PGHOST" not in serialized
    assert "PGPASSWORD" not in serialized
    assert "task299-test-password" not in serialized

    archive.write_bytes(b"tampered")
    with pytest.raises(BackupManifestError):
        verify_backup_manifest(manifest, backup_directory=tmp_path)


def test_real_postgresql_logical_backup_restore_and_restored_startup(
    postgres_server,
    monkeypatch,
    tmp_path,
):
    base_url = make_url(postgres_server.url).set(query={})
    source_database = f"task299_source_{uuid4().hex}"
    restore_database = f"task299_restore_{uuid4().hex}"
    source_url = base_url.set(database=source_database).render_as_string(hide_password=False)
    restore_url = base_url.set(database=restore_database).render_as_string(hide_password=False)
    source_dropped = False
    archive = tmp_path / "task299-recovery.dump"
    manifest_path = tmp_path / "task299-recovery.dump.manifest.json"

    _create_database(postgres_server.url, source_database)
    _create_database(postgres_server.url, restore_database)
    try:
        _upgrade_head(monkeypatch, source_url)
        expected = _seed_recovery_dataset(source_url)

        _run_pg_dump(postgres_server, source_url, archive)
        manifest = create_backup_manifest(
            archive,
            application_revision=application_head_revision(),
        )
        write_backup_manifest(manifest, manifest_path)
        verify_backup_manifest(manifest, backup_directory=tmp_path)
        assert archive.stat().st_size > 0
        assert "task299-test-password" not in manifest_path.read_text(encoding="utf-8")

        _drop_database(postgres_server.url, source_database)
        source_dropped = True

        _run_pg_restore(postgres_server, restore_url, archive)
        restored_check = check_database_schema_state(restore_url)
        assert restored_check.state is MigrationSchemaState.CURRENT
        assert restored_check.current_revisions == (application_head_revision(),)

        _verify_recovery_dataset(restore_url, expected)
        _verify_restored_constraints(restore_url, expected)
        _verify_fresh_operation_can_succeed_after_failed_operation(base_url, restore_url)

        restored_app = create_app(_production_settings(restore_url))
        with TestClient(restored_app) as client:
            assert client.get("/health").status_code == 200
    finally:
        get_settings.cache_clear()
        if not source_dropped:
            _drop_database(postgres_server.url, source_database)
        _drop_database(postgres_server.url, restore_database)


def test_ci_installs_postgresql_client_tools_for_real_recovery_path():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")

    assert "postgresql-client" in workflow
    assert "POSTGRES_TEST_DATABASE_URL" in workflow
    assert "actions/upload-artifact" not in workflow


def test_backup_artifacts_are_excluded_from_git_and_docker_contexts():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for pattern in ("backups/", "*.dump", "*.backup", "*.dump.sha256", "*.backup.sha256"):
        assert pattern in gitignore
        assert pattern in dockerignore
    assert "alembic/versions" not in gitignore
    assert "alembic/versions" not in dockerignore


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
        database_startup_max_attempts=1,
        database_startup_retry_delay_seconds=0,
        auth_token_secret=SAFE_PRODUCTION_SECRET,
        llm_mode="demo",
        outbound_webhook_url="",
        outbound_webhook_signing_enabled=True,
    )


def _seed_recovery_dataset(database_url: str) -> RecoveryDataset:
    engine = db.create_app_engine(database_url)
    workspace_slug = f"task299-{uuid4().hex}"
    try:
        with Session(engine) as session:
            workspace = Workspace(
                slug=workspace_slug,
                name="Task 299 Recovery Workspace",
                ai_estimated_spend_limit=Decimal("42.12345678"),
                ai_permitted_model_tiers=["standard", "premium"],
                ai_model_tier_downgrade_mappings={"premium": "standard"},
            )
            user = User(
                email=f"task299-{uuid4().hex}@example.test",
                display_name="Task 299 Operator",
            )
            lead = Lead(
                tenant_id=workspace_slug,
                full_name="Task 299 Fake Lead",
                company_name="Task 299 Company",
                email="task299-lead@example.test",
                source="manual",
                score=44,
            )
            session.add(workspace)
            session.add(user)
            session.add(lead)
            session.commit()
            session.refresh(workspace)
            session.refresh(user)
            session.refresh(lead)

            membership = WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceMemberRole.OWNER,
            )
            product = Product(
                tenant_id=workspace.slug,
                name="Task 299 Product",
                description="Synthetic product for backup recovery verification.",
                price=99.99,
                minimum_price=79.99,
                metadata_json={
                    "recovery": True,
                    "channels": ["console", "whatsapp_cloud"],
                    "nested": {"seats": 3},
                },
            )
            message = ConversationMessage(
                lead_id=lead.id,
                direction="inbound",
                channel="console",
                stage=SalesStage.DISCOVERY,
                content="Synthetic Task 299 recovery conversation message.",
            )
            usage = AIInvocationUsage(
                workspace_id=workspace.id,
                conversation_id=lead.id,
                task_identifier="task299",
                agent_identifier="backup-recovery",
                provider="demo",
                model="demo",
                input_tokens=10,
                output_tokens=12,
                total_tokens=22,
                latency_ms=1,
                estimated_cost=Decimal("0.01000000"),
                status=AIInvocationStatus.SUCCESSFUL,
            )
            session.add(membership)
            session.add(product)
            session.add(message)
            session.add(usage)
            session.commit()
            session.refresh(product)
            session.refresh(message)
            session.refresh(usage)

            return RecoveryDataset(
                workspace_id=workspace.id,
                workspace_slug=workspace.slug,
                user_id=user.id,
                lead_id=lead.id,
                product_id=product.id,
                message_id=message.id,
                usage_id=usage.id,
            )
    finally:
        engine.dispose()


def _verify_recovery_dataset(database_url: str, expected: RecoveryDataset) -> None:
    engine = db.create_app_engine(database_url)
    try:
        assert "alembic_version" in inspect(engine).get_table_names()
        with Session(engine) as session:
            workspace = session.exec(
                select(Workspace).where(Workspace.id == expected.workspace_id)
            ).one()
            user = session.exec(select(User).where(User.id == expected.user_id)).one()
            lead = session.exec(select(Lead).where(Lead.id == expected.lead_id)).one()
            product = session.exec(select(Product).where(Product.id == expected.product_id)).one()
            message = session.exec(
                select(ConversationMessage).where(
                    ConversationMessage.id == expected.message_id
                )
            ).one()
            usage = session.exec(
                select(AIInvocationUsage).where(AIInvocationUsage.id == expected.usage_id)
            ).one()

            assert workspace.slug == expected.workspace_slug
            assert workspace.ai_estimated_spend_limit == Decimal("42.12345678")
            assert workspace.ai_model_tier_downgrade_mappings == {"premium": "standard"}
            assert user.display_name == "Task 299 Operator"
            assert lead.tenant_id == expected.workspace_slug
            assert product.metadata_json["nested"]["seats"] == 3
            assert product.metadata_json["channels"] == ["console", "whatsapp_cloud"]
            assert message.stage is SalesStage.DISCOVERY
            assert usage.estimated_cost == Decimal("0.01000000")
            assert usage.total_tokens == 22
    finally:
        engine.dispose()


def _verify_restored_constraints(database_url: str, expected: RecoveryDataset) -> None:
    engine = db.create_app_engine(database_url)
    try:
        with Session(engine) as session:
            duplicate_workspace = Workspace(
                slug=expected.workspace_slug,
                name="Duplicate Task 299 Workspace",
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


def _verify_fresh_operation_can_succeed_after_failed_operation(base_url, restore_url: str) -> None:
    missing_url = base_url.set(
        database=f"task299_missing_{uuid4().hex}"
    ).render_as_string(hide_password=False)
    bad_engine = db.create_app_engine(missing_url)
    try:
        with pytest.raises(OperationalError), bad_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        bad_engine.dispose()

    good_engine = db.create_app_engine(restore_url)
    try:
        with good_engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        good_engine.dispose()
        with good_engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        good_engine.dispose()


def _run_pg_dump(server: PostgresServer, database_url: str, archive: Path) -> None:
    if server.container_name:
        result = subprocess.run(
            [
                "docker",
                "exec",
                *_docker_env_args(database_url, use_container_host=True),
                server.container_name,
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            pytest.fail("pg_dump failed with a non-zero exit code")
        archive.write_bytes(result.stdout)
        return

    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        pytest.skip("pg_dump is required for external PostgreSQL recovery tests")
    result = subprocess.run(
        [pg_dump, "--format=custom", "--no-owner", "--no-acl", "--file", str(archive)],
        env=_pg_env(database_url),
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail("pg_dump failed with a non-zero exit code")


def _run_pg_restore(server: PostgresServer, database_url: str, archive: Path) -> None:
    if server.container_name:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                *_docker_env_args(database_url, use_container_host=True),
                server.container_name,
                "pg_restore",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
                "--dbname",
                make_url(database_url).database or "",
            ],
            input=archive.read_bytes(),
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            pytest.fail("pg_restore failed with a non-zero exit code")
        return

    pg_restore = shutil.which("pg_restore")
    if pg_restore is None:
        pytest.skip("pg_restore is required for external PostgreSQL recovery tests")
    result = subprocess.run(
        [
            pg_restore,
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            "--dbname",
            make_url(database_url).database or "",
            str(archive),
        ],
        env=_pg_env(database_url),
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail("pg_restore failed with a non-zero exit code")


def _pg_env(database_url: str) -> dict[str, str]:
    url = make_url(database_url)
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": url.host or "",
            "PGPORT": str(url.port or 5432),
            "PGDATABASE": url.database or "",
            "PGUSER": url.username or "",
        }
    )
    if url.password:
        env["PGPASSWORD"] = url.password
    return env


def _docker_env_args(database_url: str, *, use_container_host: bool) -> list[str]:
    url = make_url(database_url)
    values = {
        "PGHOST": "127.0.0.1" if use_container_host else (url.host or ""),
        "PGPORT": "5432" if use_container_host else str(url.port or 5432),
        "PGDATABASE": url.database or "",
        "PGUSER": url.username or "",
        "PGPASSWORD": url.password or "",
    }
    args: list[str] = []
    for key, value in values.items():
        if value:
            args.extend(["-e", f"{key}={value}"])
    return args


def _create_database(admin_url: str, database_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {_quoted_database(database_name)}"))
    finally:
        engine.dispose()


def _drop_database(admin_url: str, database_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f"DROP DATABASE IF EXISTS {_quoted_database(database_name)}"))
    finally:
        engine.dispose()


def _quoted_database(database_name: str) -> str:
    if not _SAFE_DATABASE_NAME.fullmatch(database_name):
        raise ValueError("unsafe disposable database name")
    return f'"{database_name}"'


def _wait_for_postgres(database_url: str) -> None:
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.5)
        finally:
            engine.dispose()
    raise RuntimeError("Disposable PostgreSQL did not become ready") from last_error


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
