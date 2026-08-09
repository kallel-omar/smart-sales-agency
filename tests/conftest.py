import hmac
import json
import time
from collections.abc import Generator
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import IntegrationAccount


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
            webhook_hmac_secrets={"generic_hmac": "test-generic-hmac-secret"},
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
    def create(
        workspace_id,
        credential: str,
        active: bool = True,
        provider: str = "generic_hmac",
    ):
        session_dependency = app.dependency_overrides[get_session]
        with next(session_dependency()) as session:
            account = IntegrationAccount(
                workspace_id=workspace_id,
                provider=provider,
                external_account_id=f"account-{credential}",
                credential_hash=sha256(credential.encode()).hexdigest(),
                active=active,
            )
            session.add(account)
            session.commit()
            return account
    return create


@pytest.fixture
def signed_webhook_request():
    def build(
        integration_key: str,
        payload: dict,
        *,
        timestamp: int | None = None,
        signature: str | None = None,
        event_id: str | None = None,
    ) -> tuple[dict[str, str], bytes]:
        timestamp_value = timestamp if timestamp is not None else int(time.time())
        body = json.dumps(payload, separators=(",", ":")).encode()
        expected_signature = hmac.new(
            b"test-generic-hmac-secret",
            str(timestamp_value).encode() + b"." + body,
            sha256,
        ).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Integration-Key": integration_key,
            "X-Webhook-Signature": signature or expected_signature,
            "X-Webhook-Timestamp": str(timestamp_value),
        }
        if event_id:
            headers["X-Webhook-Event-Id"] = event_id
        return headers, body

    return build
