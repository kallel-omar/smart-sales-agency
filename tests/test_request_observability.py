import asyncio
import json
import logging
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import (
    REQUEST_ID_HEADER,
    JsonLogFormatter,
    RequestObservabilityMiddleware,
    get_current_request_id,
    log_structured_event,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WHATSAPP_WORKFLOW = (
    REPO_ROOT / "infra" / "n8n" / "workflows" / "task286-whatsapp-cloud-bridge.json"
)


def _completion_records(caplog):
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http_request_completed"
    ]


def _record_payload(record) -> dict:
    return getattr(record, "structured_fields")


def _serialized_logs(caplog) -> str:
    return "\n".join(
        json.dumps(
            getattr(record, "structured_fields"),
            sort_keys=True,
        )
        for record in caplog.records
        if hasattr(record, "structured_fields")
    )


def test_request_id_is_generated_returned_and_logged(client, caplog):
    caplog.set_level(logging.INFO, logger="app.http")

    response = client.get("/health")

    assert response.status_code == 200
    request_id = response.headers[REQUEST_ID_HEADER]
    UUID(request_id)
    records = _completion_records(caplog)
    assert len(records) == 1
    payload = _record_payload(records[0])
    assert payload["event"] == "http_request_completed"
    assert payload["request_id"] == request_id
    assert payload["method"] == "GET"
    assert payload["route"] == "/health"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] >= 0


def test_safe_client_request_id_is_accepted_and_malformed_value_is_replaced(
    client,
    caplog,
):
    caplog.set_level(logging.INFO, logger="app.http")

    accepted = client.get("/health", headers={REQUEST_ID_HEADER: "task291.safe-01"})
    malformed = client.get(
        "/health",
        headers={REQUEST_ID_HEADER: "unsafe request id with spaces"},
    )
    oversized = client.get("/health", headers={REQUEST_ID_HEADER: "x" * 101})

    assert accepted.headers[REQUEST_ID_HEADER] == "task291.safe-01"
    assert malformed.headers[REQUEST_ID_HEADER] != "unsafe request id with spaces"
    assert oversized.headers[REQUEST_ID_HEADER] != "x" * 101
    UUID(malformed.headers[REQUEST_ID_HEADER])
    UUID(oversized.headers[REQUEST_ID_HEADER])
    assert [record.request_id for record in _completion_records(caplog)] == [
        "task291.safe-01",
        malformed.headers[REQUEST_ID_HEADER],
        oversized.headers[REQUEST_ID_HEADER],
    ]


@pytest.mark.asyncio
async def test_concurrent_requests_keep_request_id_context_isolated():
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware)

    @app.get("/context")
    async def context():
        await asyncio.sleep(0.01)
        return {"request_id": get_current_request_id()}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        first, second = await asyncio.gather(
            async_client.get("/context", headers={REQUEST_ID_HEADER: "task291-a"}),
            async_client.get("/context", headers={REQUEST_ID_HEADER: "task291-b"}),
        )

    assert first.json()["request_id"] == "task291-a"
    assert second.json()["request_id"] == "task291-b"
    assert first.headers[REQUEST_ID_HEADER] == "task291-a"
    assert second.headers[REQUEST_ID_HEADER] == "task291-b"
    assert get_current_request_id() is None


def test_completion_log_uses_route_template_and_omits_raw_query(client, caplog):
    caplog.set_level(logging.INFO, logger="app.http")
    account_id = "11111111-1111-1111-1111-111111111111"

    response = client.get(
        (
            f"/api/integrations/accounts/{account_id}/health/runtime-readiness"
            "?customer_message=must-not-appear"
        ),
        headers={"X-Workspace-Slug": "missing-workspace"},
    )

    assert response.status_code == 404
    assert response.headers[REQUEST_ID_HEADER]
    records = _completion_records(caplog)
    assert len(records) == 1
    payload = _record_payload(records[0])
    assert payload["route"] == (
        "/api/integrations/accounts/{account_id}/health/runtime-readiness"
    )
    serialized = _serialized_logs(caplog)
    assert account_id not in serialized
    assert "customer_message" not in serialized
    assert "must-not-appear" not in serialized


def test_completion_log_is_emitted_once_for_unexpected_500(caplog):
    caplog.set_level(logging.INFO, logger="app.http")
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware)

    @app.get("/boom/{item_id}")
    def boom(item_id: str):
        assert item_id
        raise RuntimeError("task291 unexpected failure")

    with TestClient(app, raise_server_exceptions=False) as local_client:
        response = local_client.get(
            "/boom/concrete-sensitive-id",
            headers={REQUEST_ID_HEADER: "task291-500"},
        )

    assert response.status_code == 500
    records = _completion_records(caplog)
    assert len(records) == 1
    payload = _record_payload(records[0])
    assert payload["request_id"] == "task291-500"
    assert payload["route"] == "/boom/{item_id}"
    assert payload["status_code"] == 500
    assert "concrete-sensitive-id" not in _serialized_logs(caplog)


def test_service_layer_structured_logs_inherit_request_id(caplog):
    caplog.set_level(logging.INFO)
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware)
    service_logger = logging.getLogger("app.test.service")

    @app.get("/service-log")
    def service_log():
        log_structured_event(service_logger, "service_operation_completed", result="ok")
        return {"ok": True}

    with TestClient(app) as local_client:
        response = local_client.get(
            "/service-log",
            headers={REQUEST_ID_HEADER: "task291-service"},
        )

    assert response.status_code == 200
    service_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "service_operation_completed"
    ]
    assert len(service_records) == 1
    payload = _record_payload(service_records[0])
    assert payload["request_id"] == "task291-service"
    assert payload["result"] == "ok"


def test_auth_login_logs_do_not_expose_credentials_or_tokens(client, caplog):
    caplog.set_level(logging.INFO, logger="app.http")
    response = client.post(
        "/api/auth/login",
        headers={
            "Authorization": "Token task291-authorization-secret",
            REQUEST_ID_HEADER: "task291-login",
        },
        json={
            "email": "fixture-operator@example.com",
            "password": "fixture-password",
        },
    )

    assert response.status_code == 200
    access_token = response.json()["access_token"]
    serialized = _serialized_logs(caplog)
    assert "task291-login" in serialized
    assert "/api/auth/login" in serialized
    for forbidden in (
        "task291-authorization-secret",
        "fixture-operator@example.com",
        "fixture-password",
        access_token,
        "task291-authorization-secret",
        "password",
    ):
        assert forbidden not in serialized


def test_webhook_logs_do_not_expose_signatures_or_body_content(client, caplog):
    caplog.set_level(logging.INFO, logger="app.http")
    provider_delivery_marker = "provider-" + "delivery-task291"
    body = {
        "channel": "whatsapp_cloud",
        "provider_event_id": "provider-event-task291",
        "sender_external_id": "sender-task291",
        "recipient_account_id": "recipient-target-task291",
        "content": "customer-message-task291",
        "provider_delivery_id": provider_delivery_marker,
    }

    response = client.post(
        "/api/integrations/inbound-events/whatsapp-cloud",
        headers={
            "X-Integration-Key": "integration-key-task291",
            "X-Webhook-Signature": "hmac-signature-task291",
            "X-Hub-Signature-256": "meta-signature-task291",
            REQUEST_ID_HEADER: "task291-webhook",
        },
        json=body,
    )

    assert response.status_code in {401, 422}
    serialized = _serialized_logs(caplog)
    assert "task291-webhook" in serialized
    assert "/api/integrations/inbound-events/whatsapp-cloud" in serialized
    for forbidden in (
        "integration-key-task291",
        "hmac-signature-task291",
        "meta-signature-task291",
        "provider-event-task291",
        "sender-task291",
        "recipient-target-task291",
        "customer-message-task291",
        provider_delivery_marker,
        "X-Webhook-Signature",
        "X-Hub-Signature-256",
    ):
        assert forbidden not in serialized


def test_json_formatter_serializes_structured_records_safely():
    logger_name = "app.test.formatter"
    record = logging.LogRecord(
        logger_name,
        logging.INFO,
        __file__,
        1,
        "structured",
        (),
        None,
    )
    record.structured_fields = {
        "event": "formatter_test",
        "request_id": "task291-format",
        "value": object(),
    }

    formatted = JsonLogFormatter().format(record)
    payload = json.loads(formatted)

    assert payload["event"] == "formatter_test"
    assert payload["request_id"] == "task291-format"
    assert payload["logger"] == logger_name
    assert isinstance(payload["value"], str)


def test_request_observability_stays_in_fastapi_not_n8n():
    workflow_text = WHATSAPP_WORKFLOW.read_text(encoding="utf-8")

    assert "http_request_completed" not in workflow_text
    assert "X-Request-ID" not in workflow_text
    assert "LOG_FORMAT" not in workflow_text
