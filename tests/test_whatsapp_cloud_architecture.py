import json
import hmac
import re
import shutil
import subprocess
import tempfile
from hashlib import sha256
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
    outbound_node = next(
        node for node in workflow["nodes"] if node["name"] == "Prepare WhatsApp Text Send"
    )
    send_node = next(
        node for node in workflow["nodes"] if node["name"] == "Send WhatsApp Cloud Message"
    )
    normalize_node = next(
        node
        for node in workflow["nodes"]
        if node["name"] == "Normalize WhatsApp Delivery Result"
    )
    outbound_code = outbound_node["parameters"]["jsCode"]
    normalize_code = normalize_node["parameters"]["jsCode"]
    response_node = next(
        node
        for node in workflow["nodes"]
        if node["name"] == "Acknowledge WhatsApp Delivery Result"
    )

    assert workflow["id"] == "task286whatsappcloudbridge"
    assert workflow["name"] == "Task 288 WhatsApp Cloud Bridge"
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
    assert "Should Send WhatsApp Cloud Message" in node_names
    assert "WHATSAPP_CLOUD_ACCESS_TOKEN" not in outbound_code
    assert "await fetch" not in outbound_code
    assert "Authorization" not in outbound_code
    assert "WHATSAPP_CLOUD_ACCESS_TOKEN" in str(send_node["parameters"])
    assert "await fetch" not in normalize_code
    assert "messaging_product: 'whatsapp'" in outbound_code
    assert "to: recipient" in outbound_code
    assert "delivery_id: String(messageId)" in normalize_code
    assert response_node["parameters"]["options"]["responseCode"] == "={{ $json.statusCode }}"

    for forbidden in (
        "ai agent",
        "langgraph",
        "sales_stage",
        "workspace_slug",
        "workspace_id",
        "product lookup",
        "approval",
        "model selection",
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


def test_task288_outbound_workflow_uses_runtime_token_and_meta_result_boundary():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    outbound_node = next(
        node for node in workflow["nodes"] if node["name"] == "Prepare WhatsApp Text Send"
    )
    guard_node = next(
        node
        for node in workflow["nodes"]
        if node["name"] == "Should Send WhatsApp Cloud Message"
    )
    send_node = next(
        node for node in workflow["nodes"] if node["name"] == "Send WhatsApp Cloud Message"
    )
    normalize_node = next(
        node
        for node in workflow["nodes"]
        if node["name"] == "Normalize WhatsApp Delivery Result"
    )
    response_node = next(
        node
        for node in workflow["nodes"]
        if node["name"] == "Acknowledge WhatsApp Delivery Result"
    )
    outbound_code = outbound_node["parameters"]["jsCode"]
    send_parameters = send_node["parameters"]
    normalize_code = normalize_node["parameters"]["jsCode"]

    assert ".replace(/\\/$/, '')" in outbound_code
    assert ".replace(//$/, '')" not in outbound_code
    assert "await fetch" not in outbound_code
    assert "meta_request" in outbound_code
    assert "method: 'POST'" in outbound_code
    assert "messaging_product: 'whatsapp'" in outbound_code
    assert "to: recipient" in outbound_code
    assert "type: 'text'" in outbound_code
    assert "body: text" in outbound_code
    assert send_node["type"] == "n8n-nodes-base.httpRequest"
    assert send_node.get("continueOnFail") is True
    assert send_parameters["method"] == "POST"
    assert send_parameters["url"] == "={{ $json.meta_request.url }}"
    assert send_parameters["contentType"] == "raw"
    assert send_parameters["rawContentType"] == "application/json"
    assert send_parameters["body"] == "={{ JSON.stringify($json.meta_request.body) }}"
    header_values = {
        row["name"]: row["value"]
        for row in send_parameters["headerParameters"]["parameters"]
    }
    assert header_values == {
        "Authorization": "={{ 'Bearer ' + $env.WHATSAPP_CLOUD_ACCESS_TOKEN }}",
        "Content-Type": "application/json",
    }
    assert send_parameters["options"]["response"]["response"] == {
        "fullResponse": True,
        "neverError": True,
    }
    assert "WHATSAPP_CLOUD_ACCESS_TOKEN" not in outbound_code
    assert "WHATSAPP_CLOUD_ACCESS_TOKEN" not in normalize_code
    assert "const messageId = responseBody?.messages?.[0]?.id;" in normalize_code
    assert "delivery_id: String(messageId)" in normalize_code
    assert "whatsapp_meta_send_failed" in normalize_code
    assert "whatsapp_meta_request_failed" in normalize_code
    assert response_node["parameters"]["options"]["responseCode"] == "={{ $json.statusCode }}"
    delivery_header = next(
        entry
        for entry in response_node["parameters"]["options"]["responseHeaders"]["entries"]
        if entry["name"] == "x-delivery-id"
    )
    assert delivery_header["value"] == "={{ $json.delivery_id || '' }}"
    assert guard_node["parameters"]["conditions"]["conditions"][0]["leftValue"] == (
        "={{ $json.sendToMeta }}"
    )
    assert workflow["connections"]["Should Send WhatsApp Cloud Message"]["main"][0][0]["node"] == (
        "Send WhatsApp Cloud Message"
    )
    assert workflow["connections"]["Should Send WhatsApp Cloud Message"]["main"][1][0]["node"] == (
        "Acknowledge WhatsApp Delivery Result"
    )
    assert "WHATSAPP_CLOUD_ACCESS_TOKEN=replace-at-runtime" in (
        REPO_ROOT / "infra" / "n8n" / ".env.example"
    ).read_text(encoding="utf-8")
    assert "WHATSAPP_CLOUD_ACCESS_TOKEN" in (
        REPO_ROOT / "infra" / "n8n" / "compose.yml"
    ).read_text(encoding="utf-8")


def test_task288_outbound_workflow_javascript_is_syntax_valid_when_node_is_available():
    node_executable = shutil.which("node")
    if node_executable is None:
        return
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))

    for node_name in ("Prepare WhatsApp Text Send", "Normalize WhatsApp Delivery Result"):
        code_node = next(node for node in workflow["nodes"] if node["name"] == node_name)
        wrapped_code = f"async function __n8n_code_node__() {{\n{code_node['parameters']['jsCode']}\n}}\n"
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".js",
            delete=False,
            encoding="utf-8",
        ) as handle:
            handle.write(wrapped_code)
            check_file = Path(handle.name)
        try:
            completed = subprocess.run(
                [node_executable, "--check", str(check_file)],
                check=False,
                capture_output=True,
                text=True,
            )
        finally:
            check_file.unlink(missing_ok=True)

        assert completed.returncode == 0, completed.stderr or completed.stdout


def test_task288_outbound_webhook_hmac_uses_original_raw_request_bytes():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    outbound_webhook = next(
        node for node in workflow["nodes"] if node["name"] == "Outbound Transport Webhook"
    )
    outbound_node = next(
        node for node in workflow["nodes"] if node["name"] == "Prepare WhatsApp Text Send"
    )
    outbound_code = outbound_node["parameters"]["jsCode"]

    assert outbound_webhook["parameters"]["options"] == {"rawBody": True}
    assert "rawBodyBuffer = await this.helpers.getBinaryDataBuffer(0, 'data');" in outbound_code
    assert "$binary" not in outbound_code
    assert "rawBodyBase64" not in outbound_code
    assert "Buffer.from(rawBodyBase64, 'base64')" not in outbound_code
    assert "Buffer.concat([Buffer.from(String(timestamp) + '.', 'utf8'), rawBodyBuffer])" in outbound_code
    assert "JSON.stringify(bodyObject)" not in outbound_code
    assert "Buffer.from(JSON.stringify($json.body ?? {}))" not in outbound_code
    assert outbound_code.index("const expected = crypto.createHmac") < outbound_code.index("let bodyObject;")
    assert outbound_code.index("if (!secureEqual(signature, expected))") < outbound_code.index("let bodyObject;")
    assert "await fetch" not in outbound_code
    assert outbound_code.index("if (!secureEqual(signature, expected))") < outbound_code.index("meta_request")
    assert outbound_code.index("return fail(401, 'webhook_signature_mismatch'") < outbound_code.index("meta_request")
    assert "return fail(401, 'webhook_signature_missing'" in outbound_code
    assert "return fail(401, 'webhook_signature_mismatch'" in outbound_code
    assert "return fail(400, 'webhook_malformed_json'" in outbound_code
    assert "sendToMeta: false" in outbound_code
    assert "sendToMeta: true" in outbound_code
    assert workflow["connections"]["Prepare WhatsApp Text Send"]["main"][0][0]["node"] == (
        "Should Send WhatsApp Cloud Message"
    )
    assert workflow["connections"]["Should Send WhatsApp Cloud Message"]["main"][0][0]["node"] == (
        "Send WhatsApp Cloud Message"
    )


def test_task288_outbound_hmac_contract_is_byte_exact_not_semantic_json():
    secret = "matching-fastapi-n8n-secret"
    timestamp = "1700000000"
    payload = {
        "provider": "whatsapp_cloud",
        "integration_account_id": "11111111-1111-1111-1111-111111111111",
        "external_account_id": "555666777888999",
        "action_id": "22222222-2222-2222-2222-222222222222",
        "action_type": "send_message",
        "external_target_id": "15557654321",
        "content": "Hello from Task 288",
    }
    compact_body = json.dumps(payload, separators=(",", ":")).encode()
    pretty_body = json.dumps(payload, indent=2).encode()

    assert json.loads(compact_body) == json.loads(pretty_body)
    compact_signature = _outbound_hmac(secret, timestamp, compact_body)
    pretty_signature = _outbound_hmac(secret, timestamp, pretty_body)

    assert compact_signature != pretty_signature
    assert _outbound_hmac_matches(secret, timestamp, compact_body, compact_signature)
    assert not _outbound_hmac_matches(secret, timestamp, pretty_body, compact_signature)
    assert not _outbound_hmac_matches(secret, timestamp, compact_body, "0" * 64)


def test_task286_files_contain_no_real_meta_credentials_or_phone_numbers():
    task_files = [
        WORKFLOW_FILE,
        REPO_ROOT / "infra" / "n8n" / ".env.example",
        REPO_ROOT / "infra" / "n8n" / "compose.yml",
        REPO_ROOT / "docs" / "WHATSAPP_CLOUD_CHANNEL.md",
        *sorted((REPO_ROOT / "tests" / "fixtures" / "whatsapp_cloud").glob("*.json")),
    ]
    for path in task_files:
        text = path.read_text(encoding="utf-8")
        assert "EAA" not in text
        assert "Bearer EAA" not in text
        assert "sk-" not in text
        assert "xox" not in text.lower()


def _settings():
    from app.config import Settings

    return Settings(
        environment="test",
        database_url="sqlite://",
        llm_mode="demo",
        auth_token_secret="test-auth-token-secret-32-byte-value",
    )


def _outbound_hmac(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, sha256).hexdigest()


def _outbound_hmac_matches(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(_outbound_hmac(secret, timestamp, body), signature)
