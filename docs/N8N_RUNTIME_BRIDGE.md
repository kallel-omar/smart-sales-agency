# Self-hosted n8n runtime bridge

Task 285 adds an isolated, optional n8n runtime bridge for local smoke testing
the existing provider-neutral integration boundary. FastAPI remains
authoritative for user identity, workspace membership, IntegrationAccount
authentication, idempotency receipts, approvals, outbound attempts, retries,
and execution traces. n8n is only a webhook transport.

## Runtime layout

- `infra/n8n/compose.yml` starts the official pinned n8n image
  `docker.n8n.io/n8nio/n8n:2.30.5`.
- `infra/n8n/workflows/task285-runtime-bridge.json` contains one workflow with
  inbound and outbound Webhook triggers.
- `infra/n8n/.env.example` documents local runtime variables without real
  secrets.
- `scripts/smoke_n8n_runtime.py` starts a throwaway FastAPI process, loads the
  n8n workflow, publishes it, runs the smoke checks, and tears the runtime down.

The Compose file mounts a persistent `n8n_data` volume at `/home/node/.n8n`.
The smoke harness uses a unique Compose project and removes that temporary
volume on completion so generated local secrets do not linger.

## Topology

Provider-like smoke request:

`smoke harness -> n8n /webhook/task285-inbound -> FastAPI /api/integrations/inbound-events`

Outbound delivery:

`FastAPI generic_webhook adapter -> n8n /webhook/task285-outbound -> signed n8n acknowledgement`

On Docker Desktop for Windows, the n8n container reaches the host FastAPI
process through `host.docker.internal`. Override
`SSA_FASTAPI_BASE_URL_FROM_N8N` if your Docker environment uses a different host
gateway.

## Environment

The n8n runtime expects:

- `N8N_IMAGE`
- `N8N_HOST_PORT`
- `N8N_WEBHOOK_URL`
- `N8N_ENCRYPTION_KEY`
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`
- `SSA_FASTAPI_BASE_URL_FROM_N8N`
- `SSA_INBOUND_INTEGRATION_KEY`
- `SSA_INBOUND_HMAC_SECRET`
- `SSA_OUTBOUND_HMAC_SECRET`

The FastAPI side uses the existing settings:

- `AUTH_TOKEN_SECRET`
- `INTEGRATION_SECRET_TASK285_RUNTIME`
- `OUTBOUND_WEBHOOK_URL`
- `OUTBOUND_WEBHOOK_SIGNING_ENABLED=true`

Do not commit real values for any secret-bearing variable. The smoke harness
generates temporary secrets and injects them at process/container runtime.

## Running the real smoke

Install the declared project dependencies first:

```powershell
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

Then run:

```powershell
.venv/Scripts/python.exe scripts/smoke_n8n_runtime.py
```

The script proves:

- inbound n8n transport reaches FastAPI through the real
  `/api/integrations/inbound-events` boundary;
- IntegrationAccount credential and HMAC authentication are enforced by FastAPI;
- duplicate inbound delivery returns the same correlation id without duplicating
  conversation history;
- a lead from another workspace is rejected by FastAPI scoping and remains
  unmodified;
- outbound delivery is blocked until the FastAPI approval is accepted;
- FastAPI signs the generic outbound webhook request and n8n verifies it;
- delivery attempt history and execution trace records stay workspace-scoped.

Normal `pytest` remains offline: it inspects the runtime artifacts and existing
integration services but does not start Docker, reach the network, or call n8n.
