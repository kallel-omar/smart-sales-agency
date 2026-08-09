from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import IntegrationAccount
from hashlib import sha256


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


@pytest.fixture
def integration_account_factory(client):
    def create(workspace_id, credential: str, active: bool = True):
        session_dependency = app.dependency_overrides[get_session]
        with next(session_dependency()) as session:
            account = IntegrationAccount(
                workspace_id=workspace_id,
                provider="test",
                external_account_id=f"account-{credential}",
                credential_hash=sha256(credential.encode()).hexdigest(),
                active=active,
            )
            session.add(account)
            session.commit()
            return account
    return create
