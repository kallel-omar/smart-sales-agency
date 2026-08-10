import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "infra" / "n8n" / "compose.yml"
ENV_EXAMPLE = REPO_ROOT / "infra" / "n8n" / ".env.example"
WORKFLOW_FILE = REPO_ROOT / "infra" / "n8n" / "workflows" / "task285-runtime-bridge.json"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_n8n_runtime.py"


def test_n8n_runtime_configuration_is_isolated_and_pinned():
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "docker.n8n.io/n8nio/n8n:2.30.5" in compose
    assert "docker.n8n.io/n8nio/n8n:2.30.5" in env_example
    assert "n8n_data:/home/node/.n8n" in compose
    assert "./workflows:/workflows:ro" in compose
    assert "N8N_ENCRYPTION_KEY" in compose
    assert "SSA_FASTAPI_BASE_URL_FROM_N8N" in compose
    assert "SSA_INBOUND_INTEGRATION_KEY" in compose
    assert "SSA_INBOUND_HMAC_SECRET" in compose
    assert "SSA_OUTBOUND_HMAC_SECRET" in compose
    assert "replace-at-runtime" in env_example
    assert "host.docker.internal" in env_example


def test_workflow_fixture_uses_fastapi_boundaries_without_authoritative_domain_logic():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    node_types = {node["type"] for node in workflow["nodes"]}
    workflow_text = WORKFLOW_FILE.read_text(encoding="utf-8")

    assert workflow["id"] == "task285runtimebridge"
    assert workflow["active"] is True
    assert "n8n-nodes-base.webhook" in node_types
    assert "n8n-nodes-base.httpRequest" in node_types
    assert "n8n-nodes-base.respondToWebhook" in node_types
    assert nodes["Inbound Transport Webhook"]["parameters"]["path"] == "task285-inbound"
    assert nodes["Outbound Transport Webhook"]["parameters"]["path"] == "task285-outbound"
    assert "/api/integrations/inbound-events" in workflow_text
    assert "X-Integration-Key" in workflow_text
    assert "X-Webhook-Signature" in workflow_text
    assert "SSA_FASTAPI_BASE_URL_FROM_N8N" in workflow_text
    assert "$env.SSA_INBOUND_HMAC_SECRET" in workflow_text
    assert "process.env" not in workflow_text
    assert "credential" not in workflow_text.lower()
    assert "AUTH_TOKEN_SECRET" not in workflow_text
    assert "workspace_slug" not in workflow_text
    assert "workspace_id" not in workflow_text
    assert "role" not in workflow_text.lower()
    assert "sales_stage" not in workflow_text


def test_workflow_fixture_contains_no_customer_provider_credentials():
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    workflow_text = WORKFLOW_FILE.read_text(encoding="utf-8").lower()

    assert all("credentials" not in node for node in workflow["nodes"])
    assert "api_key" not in workflow_text
    assert "bearer " not in workflow_text
    assert "whatsapp" not in workflow_text
    assert "slack" not in workflow_text
    assert "gmail" not in workflow_text
    assert "hubspot" not in workflow_text


def test_smoke_harness_is_explicit_and_keeps_normal_pytest_offline():
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert 'if __name__ == "__main__"' in script
    assert "docker" in script
    assert "compose" in script
    assert "import:workflow" in script
    assert "publish:workflow" in script
    assert "/health/runtime-readiness" in script
    assert "task285-inbound" in script
    assert "task285-outbound" in script
    assert "OUTBOUND_WEBHOOK_SIGNING_ENABLED" in script
    assert "INTEGRATION_SECRET_TASK285_RUNTIME" in script


def test_core_sales_domain_has_no_n8n_dependency():
    sales_root = REPO_ROOT / "app" / "departments" / "sales"
    for path in sales_root.rglob("*.py"):
        assert "n8n" not in path.read_text(encoding="utf-8").lower()
