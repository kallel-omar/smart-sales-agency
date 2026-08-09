from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    SQLModel.metadata.create_all(test_engine)

    def get_test_session() -> Generator[Session, None, None]:
        with Session(test_engine) as session:
            yield session

    def get_test_settings() -> Settings:
        return Settings(
            environment="test",
            database_url="sqlite://",
            llm_mode="demo",
            require_human_approval=True,
            integration_dev_contexts={
                "company-a-development-key": "company-a",
                "company-b-development-key": "company-b",
            },
        )

    app.dependency_overrides[get_session] = get_test_session
    app.dependency_overrides[get_settings] = get_test_settings

    test_client = TestClient(app)

    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.clear()
        SQLModel.metadata.drop_all(test_engine)
