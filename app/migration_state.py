"""Alembic migration topology and database schema-state checks."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.database_urls import DatabaseURLConfigurationError, is_sqlite_database_url
from app.models import SQLModel

BASELINE_REVISION = "20260811_297"
_ROOT = Path(__file__).resolve().parents[1]
_ALEMBIC_INI = _ROOT / "alembic.ini"
_ALEMBIC_DIR = _ROOT / "alembic"
_SAFE_REVISION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class MigrationSchemaState(str, Enum):
    CURRENT = "current"
    UNINITIALIZED = "uninitialized"
    BEHIND = "behind"
    AHEAD_OR_UNKNOWN = "ahead_or_unknown"
    MULTIPLE_HEADS = "multiple_heads"
    CHECK_FAILED = "check_failed"


class MigrationTopologyError(RuntimeError):
    """Raised when application migration files violate the linear-head policy."""


class MigrationSchemaNotCurrentError(RuntimeError):
    """Raised when production startup sees an unsafe database schema state."""


@dataclass(frozen=True)
class MigrationTopology:
    root_revision: str
    head_revision: str
    revisions: tuple[str, ...]


@dataclass(frozen=True)
class DatabaseSchemaCheck:
    state: MigrationSchemaState
    expected_revision: str | None
    current_revisions: tuple[str, ...] = ()
    exception_type: str | None = None

    @property
    def is_current(self) -> bool:
        return self.state is MigrationSchemaState.CURRENT

    def safe_operator_message(self) -> str:
        expected = _safe_revision(self.expected_revision)
        current = _safe_revision_list(self.current_revisions)
        if self.is_current:
            return f"Database schema is current at Alembic revision {expected}."
        if self.state is MigrationSchemaState.CHECK_FAILED:
            failure = self.exception_type or "unknown_error"
            return (
                "Database schema check failed safely. "
                f"state={self.state.value} expected_revision={expected} "
                f"current_revision={current} exception_type={failure}"
            )
        return (
            "Database schema is not current. Run `alembic upgrade head` before "
            "starting the application. "
            f"state={self.state.value} expected_revision={expected} "
            f"current_revision={current}"
        )


def alembic_config() -> Config:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    return config


def script_directory(config: Config | None = None) -> ScriptDirectory:
    return ScriptDirectory.from_config(config or alembic_config())


def validate_migration_topology(script: ScriptDirectory | None = None) -> MigrationTopology:
    """Validate the committed migration graph is a single linear history."""
    try:
        directory = script or script_directory()
        heads = tuple(directory.get_heads())
        roots = tuple(directory.get_bases())
        revisions = tuple(directory.walk_revisions())
    except Exception as exc:  # noqa: BLE001
        raise MigrationTopologyError("Application migration topology could not be inspected") from exc

    revision_ids = tuple(revision.revision for revision in revisions)
    if len(set(revision_ids)) != len(revision_ids):
        raise MigrationTopologyError("Application migration topology contains duplicate revisions")
    if len(roots) != 1:
        raise MigrationTopologyError("Application migration topology must have exactly one root")
    if roots[0] != BASELINE_REVISION:
        raise MigrationTopologyError("Application migration root is not the Task 297 baseline")
    if len(heads) != 1:
        raise MigrationTopologyError("Application migration topology must have exactly one head")
    branch_points = tuple(
        revision.revision
        for revision in revisions
        if _script_boolean(revision, "is_branch_point")
    )
    merge_points = tuple(
        revision.revision
        for revision in revisions
        if _script_boolean(revision, "is_merge_point")
    )
    if branch_points or merge_points:
        raise MigrationTopologyError("Application migration topology must remain linear")

    reachable = tuple(
        revision.revision for revision in directory.iterate_revisions(heads[0], "base")
    )
    if set(reachable) != set(revision_ids):
        raise MigrationTopologyError("Application migration topology contains unreachable revisions")

    return MigrationTopology(
        root_revision=roots[0],
        head_revision=heads[0],
        revisions=tuple(reversed(reachable)),
    )


def application_head_revision() -> str:
    return validate_migration_topology().head_revision


def check_database_schema_state(database_url: str) -> DatabaseSchemaCheck:
    """Return a safe classification of the database Alembic revision state."""
    try:
        topology = validate_migration_topology()
        engine = _create_engine_for_schema_check(database_url)
        try:
            with engine.connect() as connection:
                context = MigrationContext.configure(connection)
                current_heads = tuple(str(revision) for revision in context.get_current_heads())
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001
        return DatabaseSchemaCheck(
            state=MigrationSchemaState.CHECK_FAILED,
            expected_revision=_safe_expected_revision(),
            exception_type=exc.__class__.__name__,
        )

    if not current_heads:
        return DatabaseSchemaCheck(
            state=MigrationSchemaState.UNINITIALIZED,
            expected_revision=topology.head_revision,
        )
    if len(current_heads) > 1:
        return DatabaseSchemaCheck(
            state=MigrationSchemaState.MULTIPLE_HEADS,
            expected_revision=topology.head_revision,
            current_revisions=current_heads,
        )

    current_revision = current_heads[0]
    if current_revision == topology.head_revision:
        return DatabaseSchemaCheck(
            state=MigrationSchemaState.CURRENT,
            expected_revision=topology.head_revision,
            current_revisions=current_heads,
        )
    if _is_ancestor_revision(current_revision, topology.head_revision):
        return DatabaseSchemaCheck(
            state=MigrationSchemaState.BEHIND,
            expected_revision=topology.head_revision,
            current_revisions=current_heads,
        )
    return DatabaseSchemaCheck(
        state=MigrationSchemaState.AHEAD_OR_UNKNOWN,
        expected_revision=topology.head_revision,
        current_revisions=current_heads,
    )


def ensure_database_schema_current(database_url: str) -> DatabaseSchemaCheck:
    check = check_database_schema_state(database_url)
    if not check.is_current:
        raise MigrationSchemaNotCurrentError(check.safe_operator_message())
    return check


def compare_database_schema_to_metadata(database_url: str) -> tuple:
    """Return Alembic autogenerate diffs between the database and SQLModel metadata."""
    engine = _create_engine_for_schema_check(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "include_object": _include_object,
                    "target_metadata": SQLModel.metadata,
                },
            )
            return tuple(compare_metadata(context, SQLModel.metadata))
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.migration_state",
        description="Check the configured database schema against the application Alembic head.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="verify DATABASE_URL is at the application Alembic head")
    args = parser.parse_args(argv)

    if args.command == "check":
        check = check_database_schema_state(get_settings().database_url)
        print(check.safe_operator_message())
        return 0 if check.is_current else 1
    return 1


def _create_engine_for_schema_check(database_url: str) -> Engine:
    kwargs: dict = {"echo": False}
    try:
        if is_sqlite_database_url(database_url):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_pre_ping"] = True
    except DatabaseURLConfigurationError:
        pass
    return create_engine(database_url, **kwargs)


def _is_ancestor_revision(candidate_revision: str, head_revision: str) -> bool:
    try:
        revisions = script_directory().iterate_revisions(head_revision, "base")
        return candidate_revision in {revision.revision for revision in revisions}
    except Exception:  # noqa: BLE001
        return False


def _safe_expected_revision() -> str | None:
    try:
        return application_head_revision()
    except Exception:  # noqa: BLE001
        return None


def _safe_revision(value: str | None) -> str:
    if value is None:
        return "unknown"
    if _SAFE_REVISION_PATTERN.fullmatch(value):
        return value
    return "<unsafe-revision>"


def _safe_revision_list(values: tuple[str, ...]) -> str:
    if not values:
        return "none"
    return ",".join(_safe_revision(value) for value in values)


def _script_boolean(revision, attribute: str) -> bool:
    value = getattr(revision, attribute, False)
    if callable(value):
        return bool(value())
    return bool(value)


def _include_object(_object, name: str | None, type_: str, _reflected, _compare_to) -> bool:
    return not (type_ == "table" and name == "alembic_version")


if __name__ == "__main__":
    raise SystemExit(main())
