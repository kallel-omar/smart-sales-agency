"""Run the Task 285 self-hosted n8n runtime smoke test.

The smoke harness intentionally uses only public HTTP boundaries:
FastAPI owns identity, workspace scope, IntegrationAccount auth, idempotency,
approval state, and delivery attempts. n8n is started as a local Docker runtime
and acts only as a webhook transport.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
N8N_DIR = REPO_ROOT / "infra" / "n8n"
WORKFLOW_ID = "task285runtimebridge"
N8N_IMAGE = "docker.n8n.io/n8nio/n8n:2.30.5"
SECRET_REFERENCE = "INTEGRATION_SECRET_TASK285_RUNTIME"


class SmokeFailure(RuntimeError):
    """Raised when the real n8n runtime smoke cannot prove the bridge."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: Any
    headers: dict[str, str]
    raw: str


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _api_headers(token: str, workspace_slug: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if workspace_slug is not None:
        headers["X-Workspace-Slug"] = workspace_slug
    return headers


def _request_json(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
    expected_statuses: set[int] | None = None,
) -> HttpResult:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    data = None
    if json_body is not None:
        data = json.dumps(json_body, separators=(",", ":")).encode()
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_headers = {key.lower(): value for key, value in exc.headers.items()}
        raw = exc.read().decode("utf-8")
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as exc:
        raise SmokeFailure(f"HTTP request failed for {url}: {exc}") from exc

    parsed: Any
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
    else:
        parsed = None

    if expected_statuses is not None and status not in expected_statuses:
        raise SmokeFailure(f"{method} {url} returned {status}: {raw[:500]}")
    return HttpResult(status=status, body=parsed, headers=response_headers, raw=raw)


def _wait_for_http(url: str, *, timeout_seconds: int, accepted_statuses: set[int]) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            result = _request_json("GET", url, timeout=5)
            if result.status in accepted_statuses:
                return
            last_error = f"status {result.status}"
        except SmokeFailure as exc:
            last_error = str(exc)
        time.sleep(1)
    raise SmokeFailure(f"Timed out waiting for {url}: {last_error}")


def _wait_for_n8n_ready(n8n_port: int, *, timeout_seconds: int) -> None:
    base_url = f"http://127.0.0.1:{n8n_port}"
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        for path, statuses in (
            ("/healthz", {200}),
            ("/", {200, 302, 401}),
        ):
            try:
                result = _request_json("GET", f"{base_url}{path}", timeout=5)
            except SmokeFailure as exc:
                last_error = str(exc)
                continue
            lowered = result.raw.lower()
            if result.status in statuses and "starting up" not in lowered and "please wait" not in lowered:
                return
            last_error = f"{path} returned {result.status}: {result.raw[:120]}"
        time.sleep(1)
    raise SmokeFailure(f"Timed out waiting for n8n readiness: {last_error}")


def _wait_for_n8n_webhook_ready(n8n_port: int, *, timeout_seconds: int) -> None:
    url = f"http://127.0.0.1:{n8n_port}/webhook/task285-inbound"
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            result = _request_json(
                "POST",
                url,
                json_body={},
                headers={"X-Integration-Event-Id": "task285-runtime-readiness-probe"},
                timeout=20,
            )
        except SmokeFailure as exc:
            last_error = str(exc)
            time.sleep(1)
            continue
        if result.status == 200 and "statusCode" in result.raw:
            return
        last_error = f"status {result.status}: {result.raw[:200]}"
        time.sleep(1)
    raise SmokeFailure(f"Timed out waiting for n8n production webhook: {last_error}")


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path = REPO_ROOT,
    timeout_seconds: int = 300,
    allowed_exit_codes: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    allowed = allowed_exit_codes or {0}
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode not in allowed:
        safe_command = " ".join(command)
        raise SmokeFailure(
            f"Command failed ({completed.returncode}): {safe_command}\n"
            f"{completed.stdout[-4000:]}"
        )
    return completed


def _start_fastapi(
    *,
    api_port: int,
    n8n_port: int,
    auth_secret: str,
    hmac_secret: str,
    temp_dir: Path,
) -> tuple[subprocess.Popen[bytes], Path]:
    db_path = temp_dir / "task285-fastapi.sqlite3"
    api_log = temp_dir / "fastapi.log"
    env = os.environ.copy()
    env.update(
        {
            "APP_NAME": "Smart Sales Agency Task 285 Smoke",
            "ENVIRONMENT": "development",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "LLM_MODE": "demo",
            "REQUIRE_HUMAN_APPROVAL": "true",
            "AUTH_TOKEN_SECRET": auth_secret,
            SECRET_REFERENCE: hmac_secret,
            "OUTBOUND_WEBHOOK_URL": f"http://127.0.0.1:{n8n_port}/webhook/task285-outbound",
            "OUTBOUND_WEBHOOK_CONNECT_TIMEOUT_SECONDS": "5",
            "OUTBOUND_WEBHOOK_READ_TIMEOUT_SECONDS": "30",
            "OUTBOUND_WEBHOOK_SIGNING_ENABLED": "true",
        }
    )
    log_handle = api_log.open("wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(api_port),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    log_handle.close()
    try:
        _wait_for_http(
            f"http://127.0.0.1:{api_port}/health",
            timeout_seconds=60,
            accepted_statuses={200},
        )
    except Exception:
        _stop_process(process)
        tail = api_log.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise SmokeFailure(f"FastAPI did not become ready:\n{tail}") from None
    return process, api_log


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def _seed_fastapi(api_base_url: str) -> dict[str, Any]:
    suffix = secrets.token_hex(5)
    email = f"task285-{suffix}@example.com"
    password = f"task285-password-{suffix}"
    register = _request_json(
        "POST",
        f"{api_base_url}/api/auth/register",
        json_body={"email": email, "password": password, "display_name": "Task 285 Operator"},
        expected_statuses={201},
    )
    login = _request_json(
        "POST",
        f"{api_base_url}/api/auth/login",
        json_body={"email": email, "password": password},
        expected_statuses={200},
    )
    token = login.body["access_token"]
    me = _request_json(
        "GET",
        f"{api_base_url}/api/auth/me",
        headers=_api_headers(token),
        expected_statuses={200},
    )
    if me.body["id"] != register.body["id"]:
        raise SmokeFailure("/api/auth/me did not resolve the registered active user")

    workspace_a_slug = f"task285-a-{suffix}"
    workspace_b_slug = f"task285-b-{suffix}"
    workspace_a = _request_json(
        "POST",
        f"{api_base_url}/api/workspaces",
        headers=_api_headers(token),
        json_body={"slug": workspace_a_slug, "name": "Task 285 A"},
        expected_statuses={201},
    ).body
    workspace_b = _request_json(
        "POST",
        f"{api_base_url}/api/workspaces",
        headers=_api_headers(token),
        json_body={"slug": workspace_b_slug, "name": "Task 285 B"},
        expected_statuses={201},
    ).body

    lead_a = _request_json(
        "POST",
        f"{api_base_url}/api/leads",
        headers=_api_headers(token, workspace_a_slug),
        json_body={
            "full_name": "Ada Buyer",
            "company_name": "Boundary Labs",
            "email": f"ada-{suffix}@example.com",
            "source": "task285-smoke",
            "notes": "Valid inbound lead for the n8n runtime bridge.",
        },
        expected_statuses={201},
    ).body
    lead_b = _request_json(
        "POST",
        f"{api_base_url}/api/leads",
        headers=_api_headers(token, workspace_b_slug),
        json_body={
            "full_name": "Grace Buyer",
            "company_name": "Isolation Labs",
            "email": f"grace-{suffix}@example.com",
            "source": "task285-smoke",
            "notes": "Cross-workspace lead used to prove FastAPI scoping.",
        },
        expected_statuses={201},
    ).body

    inbound_account = _request_json(
        "POST",
        f"{api_base_url}/api/integrations/accounts",
        headers=_api_headers(token, workspace_a_slug),
        json_body={
            "provider": "generic_hmac",
            "external_account_id": "task285-inbound-runtime",
            "secret_reference": SECRET_REFERENCE,
        },
        expected_statuses={201},
    ).body
    outbound_account = _request_json(
        "POST",
        f"{api_base_url}/api/integrations/accounts",
        headers=_api_headers(token, workspace_a_slug),
        json_body={
            "provider": "generic_webhook",
            "external_account_id": "task285-outbound-runtime",
            "secret_reference": SECRET_REFERENCE,
        },
        expected_statuses={201},
    ).body

    readiness = _request_json(
        "GET",
        (
            f"{api_base_url}/api/integrations/accounts/{inbound_account['id']}"
            "/health/runtime-readiness"
        ),
        headers=_api_headers(token, workspace_a_slug),
        expected_statuses={200},
    ).body
    if readiness["id"] != inbound_account["id"] or readiness["status"] != "ready":
        raise SmokeFailure(f"Integration runtime readiness failed: {readiness}")

    return {
        "token": token,
        "workspace_a_slug": workspace_a_slug,
        "workspace_b_slug": workspace_b_slug,
        "workspace_a_id": workspace_a["id"],
        "workspace_b_id": workspace_b["id"],
        "lead_a_id": lead_a["id"],
        "lead_b_id": lead_b["id"],
        "inbound_account_id": inbound_account["id"],
        "inbound_credential": inbound_account["inbound_credential"],
        "outbound_account_id": outbound_account["id"],
    }


def _n8n_env(
    *,
    n8n_port: int,
    api_port: int,
    integration_key: str,
    hmac_secret: str,
    suffix: str,
    fastapi_base_url_from_n8n: str | None,
) -> dict[str, str]:
    base_url_from_n8n = fastapi_base_url_from_n8n or f"http://host.docker.internal:{api_port}"
    env = os.environ.copy()
    env.update(
        {
            "N8N_IMAGE": N8N_IMAGE,
            "N8N_CONTAINER_NAME": f"smart-sales-task285-n8n-{suffix}",
            "N8N_HOST_PORT": str(n8n_port),
            "N8N_WEBHOOK_URL": f"http://localhost:{n8n_port}/",
            "N8N_ENCRYPTION_KEY": secrets.token_urlsafe(32),
            "SSA_FASTAPI_BASE_URL_FROM_N8N": base_url_from_n8n,
            "SSA_INBOUND_INTEGRATION_KEY": integration_key,
            "SSA_INBOUND_HMAC_SECRET": hmac_secret,
            "SSA_OUTBOUND_HMAC_SECRET": hmac_secret,
            "GENERIC_TIMEZONE": "UTC",
            "TZ": "UTC",
        }
    )
    return env


def _compose(
    args: list[str],
    *,
    project_name: str,
    env: dict[str, str],
    timeout_seconds: int = 300,
    allowed_exit_codes: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["docker", "compose", "-p", project_name, "-f", "compose.yml", *args],
        env=env,
        cwd=N8N_DIR,
        timeout_seconds=timeout_seconds,
        allowed_exit_codes=allowed_exit_codes,
    )


def _start_n8n(
    *,
    n8n_port: int,
    project_name: str,
    env: dict[str, str],
) -> None:
    _compose(["down", "--volumes", "--remove-orphans"], project_name=project_name, env=env)
    _compose(["pull", "n8n"], project_name=project_name, env=env, timeout_seconds=600)
    _compose(
        ["run", "--rm", "n8n", "import:workflow", "--input=/workflows/task285-runtime-bridge.json"],
        project_name=project_name,
        env=env,
        timeout_seconds=240,
    )
    _compose(
        ["run", "--rm", "n8n", "publish:workflow", f"--id={WORKFLOW_ID}"],
        project_name=project_name,
        env=env,
        timeout_seconds=240,
    )
    _compose(["up", "-d", "n8n"], project_name=project_name, env=env, timeout_seconds=240)
    _wait_for_n8n_ready(n8n_port, timeout_seconds=180)
    _wait_for_n8n_webhook_ready(n8n_port, timeout_seconds=120)
def _unwrap_n8n_boundary_response(result: HttpResult) -> tuple[int, Any]:
    payload = result.body
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        raise SmokeFailure(f"n8n returned a non-object response: {result.raw[:500]}")
    status = int(payload.get("statusCode", result.status))
    body = payload.get("body", payload)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
    return status, body


def _run_bridge_checks(
    *,
    api_base_url: str,
    n8n_base_url: str,
    seeded: dict[str, Any],
) -> dict[str, Any]:
    inbound_payload = {
        "lead_id": seeded["lead_a_id"],
        "channel": "website_chat",
        "content": "Can you confirm pricing for the monthly plan?",
    }
    inbound_headers = {"X-Integration-Event-Id": "task285-runtime-inbound"}
    inbound = _request_json(
        "POST",
        f"{n8n_base_url}/webhook/task285-inbound",
        json_body=inbound_payload,
        headers=inbound_headers,
        expected_statuses={200},
        timeout=60,
    )
    inbound_status, inbound_body = _unwrap_n8n_boundary_response(inbound)
    if inbound_status != 200 or not isinstance(inbound_body, dict):
        raise SmokeFailure(f"Inbound n8n bridge failed: {inbound.raw[:500]}")
    correlation_id = inbound_body.get("correlation_id")
    if not correlation_id:
        raise SmokeFailure(f"Inbound response did not include correlation_id: {inbound_body}")

    duplicate = _request_json(
        "POST",
        f"{n8n_base_url}/webhook/task285-inbound",
        json_body=inbound_payload,
        headers=inbound_headers,
        expected_statuses={200},
        timeout=60,
    )
    duplicate_status, duplicate_body = _unwrap_n8n_boundary_response(duplicate)
    if (
        duplicate_status != 200
        or not isinstance(duplicate_body, dict)
        or duplicate_body.get("duplicate") is not True
        or duplicate_body.get("correlation_id") != correlation_id
    ):
        raise SmokeFailure(f"Duplicate inbound idempotency failed: {duplicate.raw[:500]}")

    history = _request_json(
        "GET",
        f"{api_base_url}/api/conversations/{seeded['lead_a_id']}",
        headers=_api_headers(seeded["token"], seeded["workspace_a_slug"]),
        expected_statuses={200},
    ).body
    inbound_count = len([row for row in history if row["direction"] == "inbound"])
    if inbound_count != 1:
        raise SmokeFailure(f"Duplicate inbound created {inbound_count} inbound messages")

    isolation_payload = {
        "lead_id": seeded["lead_b_id"],
        "channel": "website_chat",
        "content": "This lead belongs to another workspace.",
    }
    isolation = _request_json(
        "POST",
        f"{n8n_base_url}/webhook/task285-inbound",
        json_body=isolation_payload,
        headers={"X-Integration-Event-Id": "task285-runtime-isolation"},
        expected_statuses={200},
        timeout=60,
    )
    isolation_status, isolation_body = _unwrap_n8n_boundary_response(isolation)
    if isolation_status != 404:
        raise SmokeFailure(f"Workspace isolation failed: {isolation_status} {isolation_body}")
    cross_history = _request_json(
        "GET",
        f"{api_base_url}/api/conversations/{seeded['lead_b_id']}",
        headers=_api_headers(seeded["token"], seeded["workspace_b_slug"]),
        expected_statuses={200},
    ).body
    if cross_history:
        raise SmokeFailure("Cross-workspace inbound event mutated the other workspace")

    action = _request_json(
        "POST",
        f"{api_base_url}/api/integrations/accounts/{seeded['outbound_account_id']}/outbound-actions",
        headers=_api_headers(seeded["token"], seeded["workspace_a_slug"]),
        json_body={
            "external_target_id": "task285-recipient",
            "action_type": "send_message",
            "content": "Approved Task 285 outbound delivery through n8n.",
            "payload": {"transport": "n8n"},
            "correlation_id": correlation_id,
            "idempotency_key": "task285-runtime-outbound",
            "requires_approval": True,
        },
        expected_statuses={201},
    ).body
    if not action.get("approval_request_id"):
        raise SmokeFailure("Approved outbound smoke action did not create an approval gate")

    blocked = _request_json(
        "POST",
        (
            f"{api_base_url}/api/integrations/accounts/{seeded['outbound_account_id']}"
            f"/outbound-actions/{action['id']}/deliver"
        ),
        headers=_api_headers(seeded["token"], seeded["workspace_a_slug"]),
        expected_statuses={409},
    )
    if "requires approval" not in str(blocked.body):
        raise SmokeFailure(f"Delivery was not blocked before approval: {blocked.raw[:500]}")

    _request_json(
        "POST",
        f"{api_base_url}/api/approvals/{action['approval_request_id']}/approve",
        headers=_api_headers(seeded["token"], seeded["workspace_a_slug"]),
        json_body={"reviewer_note": "Task 285 smoke approved."},
        expected_statuses={200},
    )
    delivered = _request_json(
        "POST",
        (
            f"{api_base_url}/api/integrations/accounts/{seeded['outbound_account_id']}"
            f"/outbound-actions/{action['id']}/deliver"
        ),
        headers=_api_headers(seeded["token"], seeded["workspace_a_slug"]),
        expected_statuses={200},
        timeout=60,
    ).body
    if delivered["status"] != "delivered" or not str(delivered.get("provider_delivery_id", "")).startswith(
        "n8n-task285-"
    ):
        raise SmokeFailure(f"Outbound delivery through n8n failed: {delivered}")

    attempts = _request_json(
        "GET",
        (
            f"{api_base_url}/api/integrations/accounts/{seeded['outbound_account_id']}"
            f"/outbound-actions/{action['id']}/delivery-attempts"
        ),
        headers=_api_headers(seeded["token"], seeded["workspace_a_slug"]),
        expected_statuses={200},
    ).body
    if len(attempts) != 1 or attempts[0]["status"] != "delivered":
        raise SmokeFailure(f"Delivery attempt history did not record one success: {attempts}")

    trace = _request_json(
        "GET",
        f"{api_base_url}/api/integrations/execution-traces/{correlation_id}",
        headers=_api_headers(seeded["token"], seeded["workspace_a_slug"]),
        expected_statuses={200},
    ).body
    if trace["inbound"]["correlation_id"] != correlation_id:
        raise SmokeFailure(f"Trace did not resolve the inbound receipt: {trace}")
    if not trace["outbound_actions"] or trace["outbound_actions"][0]["status"] != "delivered":
        raise SmokeFailure(f"Trace did not include delivered outbound action: {trace}")

    return {
        "inbound_status": inbound_status,
        "duplicate": True,
        "workspace_isolation_status": isolation_status,
        "approval_gate_status": blocked.status,
        "outbound_status": delivered["status"],
        "provider_delivery_id": delivered["provider_delivery_id"],
        "delivery_attempts": len(attempts),
        "trace_outbound_actions": len(trace["outbound_actions"]),
        "correlation_id": correlation_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fastapi-port", type=int, default=0)
    parser.add_argument("--n8n-port", type=int, default=0)
    parser.add_argument("--fastapi-base-url-from-n8n", default=None)
    parser.add_argument("--keep-runtime", action="store_true")
    args = parser.parse_args()

    api_port = args.fastapi_port or _free_port()
    n8n_port = args.n8n_port or _free_port()
    api_base_url = f"http://127.0.0.1:{api_port}"
    n8n_base_url = f"http://127.0.0.1:{n8n_port}"
    suffix = secrets.token_hex(4)
    auth_secret = secrets.token_urlsafe(32)
    hmac_secret = secrets.token_urlsafe(32)
    fastapi_process: subprocess.Popen[bytes] | None = None
    project_name: str | None = None
    n8n_env: dict[str, str] | None = None

    with tempfile.TemporaryDirectory(prefix="task285-n8n-smoke-", ignore_cleanup_errors=True) as tmp:
        temp_dir = Path(tmp)
        try:
            fastapi_process, api_log = _start_fastapi(
                api_port=api_port,
                n8n_port=n8n_port,
                auth_secret=auth_secret,
                hmac_secret=hmac_secret,
                temp_dir=temp_dir,
            )
            seeded = _seed_fastapi(api_base_url)
            project_name = f"ssa_task285_{suffix}"
            n8n_env = _n8n_env(
                n8n_port=n8n_port,
                api_port=api_port,
                integration_key=seeded["inbound_credential"],
                hmac_secret=hmac_secret,
                suffix=suffix,
                fastapi_base_url_from_n8n=args.fastapi_base_url_from_n8n,
            )
            _start_n8n(
                n8n_port=n8n_port,
                project_name=project_name,
                env=n8n_env,
            )
            results = _run_bridge_checks(
                api_base_url=api_base_url,
                n8n_base_url=n8n_base_url,
                seeded=seeded,
            )
            safe_summary = {
                "result": "ok",
                "image": N8N_IMAGE,
                "fastapi_base_url": api_base_url,
                "n8n_base_url": n8n_base_url,
                "workspace_a": seeded["workspace_a_slug"],
                "workspace_b": seeded["workspace_b_slug"],
                "inbound_account_id": seeded["inbound_account_id"],
                "outbound_account_id": seeded["outbound_account_id"],
                **results,
            }
            print(json.dumps(safe_summary, indent=2, sort_keys=True))
            if args.keep_runtime:
                print(f"Keeping n8n project {project_name} and FastAPI process running for inspection.")
            return 0
        except SmokeFailure as exc:
            print(f"Task 285 n8n smoke failed: {exc}", file=sys.stderr)
            if fastapi_process is not None:
                log_path = temp_dir / "fastapi.log"
                if log_path.exists():
                    print(log_path.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
            return 1
        finally:
            if not args.keep_runtime:
                if project_name and n8n_env:
                    _compose(
                        ["down", "--volumes", "--remove-orphans"],
                        project_name=project_name,
                        env=n8n_env,
                        timeout_seconds=120,
                        allowed_exit_codes={0, 1},
                    )
                _stop_process(fastapi_process)


if __name__ == "__main__":
    raise SystemExit(main())
