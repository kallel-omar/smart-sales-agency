"""Small database URL helpers shared by runtime validation and engine setup."""

from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class DatabaseURLConfigurationError(ValueError):
    """Raised when DATABASE_URL cannot be parsed safely."""


def database_dialect_name(database_url: str) -> str:
    """Return the SQLAlchemy backend name without exposing URL credentials."""
    try:
        return make_url(database_url).get_backend_name()
    except ArgumentError as exc:
        raise DatabaseURLConfigurationError("DATABASE_URL is malformed") from exc


def is_sqlite_database_url(database_url: str) -> bool:
    return database_dialect_name(database_url) == "sqlite"


def is_postgresql_database_url(database_url: str) -> bool:
    return database_dialect_name(database_url) == "postgresql"
