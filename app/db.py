from collections.abc import Generator

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app import models as _models  # noqa: F401  Ensures SQLModel metadata is populated.
from app.config import get_settings
from app.database_urls import is_sqlite_database_url

settings = get_settings()
engine = None


def engine_kwargs_for_url(database_url: str) -> dict:
    """Return dialect-specific SQLAlchemy engine options without leaking URLs."""
    if is_sqlite_database_url(database_url):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


def create_app_engine(database_url: str) -> Engine:
    return create_engine(database_url, echo=False, **engine_kwargs_for_url(database_url))


engine = create_app_engine(settings.database_url)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
