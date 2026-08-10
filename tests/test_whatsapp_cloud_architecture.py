import json
import re
from pathlib import Path

from app.services.delivery_adapters import (
    GenericWebhookDeliveryAdapter,
    default_delivery_adapter_registry,
)
from app.services.webhook_authentication import ProviderWebhookAuthenticationService

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_FILE = REPO_ROOT / "infra" / "n8n" / "workflows" / "task286-whatsapp-cloud-bridge.json"
TASK285_WORKFLOW = REPO_ROOT / "infra" / "n8n" / "workflows" / "task285-runtime-bridge.json"


def test_whatsapp_provider_reuses_existing_integration_account_contracts():
    verifier_service = ProviderWebhookAuthenticationService(settings=_settings())
    webhook_adapter = GenericWebhookDeliveryAdapter("https://n8n.test/webhook")
    registry = default_delivery_adapter_registry(webhook_adapter)

    assert "whatsapp_cloud" in verifier_service.verifiers
    assert registry.get("whatsapp_cloud") is webhook_adapter

    models_text = (REPO_ROOT / "app" / "models.py").read_text(encoding="utf-8").lower()
    assert "whatsapp_cloud" not in models_text
    assert "phone_number_id" not in models_text


def test_whatsapp_specific_code_stays_outside_sales_and_ai_domain_logic():
    forbidden = re.compile(r"\bmeta\b|whatsapp", re.IGNORECASE)
    sales_root = REPO_ROOT / "app" / "departments" / "sales"
    for path in sales_root.rglob("*.py"):
        assert forbidden.search(path.read_text(encoding="utf-8")) is None

    ai_gateway = (REPO_ROOT / "app" / "services" / "ai_invocation_gateway.py").read_text(
        encoding="utf-8"
    )
    assert forbidden.search(ai_gateway) is None


def test_task286_workflow_is_transport_only_and_keeps_fastapi_as_authority():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    workflow_text = WORKFLOW_FILE.read_text(encoding="utf-8")
    lower_text = workflow_text.lower()
    node_types = {node["type"] for node in workflow["nodes"]}
    node_names = {node["name"] for node in workflow["nodes"]}

    assert workflow["id"] == "task286whatsappcloudbridge"
    assert workflow["active"] is True
    assert "n8n-nodes-base.webhook" in node_types
    assert "n8n-nodes-base.code" in node_types
    assert "n8n-nodes-base.httpRequest" in node_types
    assert "n8n-nodes-base.respondToWebhook" in node_types
    assert {"Meta Verification Webhook", "Meta Event Webhook", "Outbound Transport Webhook"} <= node_names
    assert "rawBody" in workflow_text
    assert "hub.challenge" in workflow_text
    assert "x-hub-signature-256" in lower_text
    assert "/api/integrations/inbound-events/whatsapp-cloud" in workflow_text
    assert "X-Integration-Key" in workflow_text
    assert "X-Webhook-Signature" in workflow_text
    assert "provider_event_id" in workflow_text
    assert "external_target_id" in workflow_text
    assert "action_type" in workflow_text
    assert "recipient_type" in workflow_text

    for forbidden in (
        "ai agent",
        "langgraph",
        "sales_stage",
        "workspace_slug",
        "workspace_id",
        "product lookup",
        "approval",
        "model selection",
        "bearer ",
        "access_token",
        "process.env",
    ):
        assert forbidden not in lower_text
    assert all("credentials" not in node for node in workflow["nodes"])


def test_task251_and_task285_boundaries_remain_visible():
    route_text = (REPO_ROOT / "app" / "api" / "routes" / "whatsapp_cloud.py").read_text(
        encoding="utf-8"
    )
    assert "reserve_event" in route_text
    assert "InboundIntegrationService" in route_text
    assert "Lead.phone" in route_text
    assert TASK285_WORKFLOW.exists()
    assert json.loads(TASK285_WORKFLOW.read_text(encoding="utf-8"))["id"] == (
        "task285runtimebridge"
    )


def test_task286_files_contain_no_real_meta_credentials_or_phone_numbers():
    task_files = [
        WORKFLOW_FILE,
        REPO_ROOT / "docs" / "WHATSAPP_CLOUD_CHANNEL.md",
        *sorted((REPO_ROOT / "tests" / "fixtures" / "whatsapp_cloud").glob("*.json")),
    ]
    for path in task_files:
        text = path.read_text(encoding="utf-8")
        assert "EAA" not in text
        assert "Bearer " not in text
        assert "xox" not in text.lower()


def _settings():
    from app.config import Settings

    return Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="demo",
        auth_token_secret="test-auth-token-secret-32-byte-value",
    )
