# Backend Production Readiness

Task 300 is the final backend acceptance boundary before frontend work begins.
It validates the backend as a connected production system, not a complete
commercial launch. Frontend, billing, and deployment infrastructure remain
separate work.

## Final Status

| Item | Status |
| --- | --- |
| Phase A manual local production backend acceptance | PASSED |
| Phase B real WhatsApp Cloud end-to-end acceptance | PASSED |
| Backend production readiness | PASSED |
| Frontend | NOT YET IMPLEMENTED |
| Billing/subscription | NOT YET IMPLEMENTED |

Task 300 backend acceptance is complete. The backend is ready for Task 301
frontend work, with billing/subscription and frontend implementation still
outside the completed backend scope.

## Readiness Matrix

| Area | Status | Notes |
| --- | --- | --- |
| authentication | READY | Credential-backed users, Argon2 password hashes, signed expiring bearer tokens, and inactive-user rejection are covered. |
| RBAC | READY | Persisted workspace membership and role policy remain the authority; JWT claims do not carry workspace power. |
| workspace isolation | READY | Workspace selection is server-authenticated and scoped; cross-workspace reads, approvals, integrations, and callbacks fail safely. |
| products/leads | READY | Tenant-owned product and lead data are scoped by authenticated workspace context, not body tenant fields. |
| conversations | READY | Sales turns persist inbound/outbound history and preserve idempotency boundaries. |
| approvals | READY | Human approval requests persist and block outbound delivery until an authorized reviewer approves. |
| AI orchestration | READY | LangGraph/Sales orchestration uses the AI gateway for non-demo LLM work and deterministic demo behavior for offline tests. |
| Tunisian/Arabizi behavior | READY | Language/script selection supports representative Tunisian Arabizi turns without brittle exact-response matching. |
| AI accounting/cost limits | READY | AI invocation usage, token/cost accounting, model routing, downgrade/block policy, and workspace limits are covered. |
| WhatsApp inbound boundary | READY | Provider-neutral inbound idempotency accepts real WhatsApp event IDs and resolves workspace/account server-side. |
| WhatsApp outbound boundary | READY | FastAPI owns outbound actions, approvals, retries, state, and audit; n8n remains transport-only. |
| provider status callbacks | READY | Sent/delivered/read/failed callbacks persist with provider chronology, idempotency, and account scoping. |
| integration readiness | READY | Runtime readiness reports configuration state without provider network probes. |
| request observability | READY | Structured request-completion logs preserve request IDs, route templates, status, and duration without bodies or secrets. |
| metrics | READY | Prometheus HTTP metrics use bounded method/route/status labels and avoid tenant/customer/request identifiers. |
| rate limiting | READY WITH DOCUMENTED LIMITATION | Task 293 is process-local and memory-backed; one FastAPI process is required for deterministic enforcement. |
| safe errors | READY | Unexpected production errors return generic 500 responses with request IDs and no raw exception details. |
| production configuration | READY | Production validates APP_ENV, AUTH_TOKEN_SECRET, PostgreSQL DATABASE_URL, CORS, docs policy, and outbound signing. |
| production Docker runtime | READY | Image runs Uvicorn without reload, as non-root app user, with local /health healthcheck. |
| PostgreSQL | READY | Production requires PostgreSQL; SQLite remains development/test only. |
| migrations | READY | Alembic head is the production schema authority; FastAPI does not auto-migrate. |
| migration lifecycle guard | READY | Startup refuses uninitialized, behind, unknown/ahead, multiple-head, or failed schema states. |
| DB startup resilience | READY | Only transient schema-check failures are retried with bounded attempts and safe logs. |
| backup/restore | READY WITH DOCUMENTED LIMITATION | Logical backup/restore runbook and artifact exclusions exist; PITR, snapshots, replication, and failover are deployment-owned. |
| CI | READY | CI runs pytest with disposable PostgreSQL and builds the production Docker image without external credentials. |
| n8n boundary | READY | n8n remains the replaceable WhatsApp transport and owns Meta access tokens at runtime only. |
| Phase A manual acceptance | PASSED | Local production-like backend artifact acceptance passed with disposable resources only. |
| Phase B real WhatsApp acceptance | PASSED | Real inbound, approval, outbound, delivery, and provider-status callback flow passed through the existing architecture. |
| backend production readiness | PASSED | Automated and manual backend acceptance are complete. |
| frontend | NOT YET IMPLEMENTED | Frontend implementation starts after Task 300. |
| billing/subscriptions | NOT YET IMPLEMENTED | Billing is intentionally outside Tasks 285-300. |

## Backend Go Criteria

Backend GO means the FastAPI backend is ready for Task 301 frontend work when:

- automated Task 300 E2E passes against real PostgreSQL after `alembic upgrade head`
- production startup refuses an empty PostgreSQL schema and accepts only current Alembic head
- auth, RBAC, workspace isolation, integration account scoping, approval gates, AI accounting, idempotency, provider callbacks, metrics, rate limiting, and safe errors all pass
- the Docker image builds and preserves the production runtime contract
- no secrets, live identifiers, customer content, database archives, or provider credentials appear in source, docs, tests, or Docker build context

Critical no-go blockers include workspace isolation failure, auth/RBAC bypass,
approval bypass, duplicate inbound side effects, unsafe provider callback
correlation, missing AI accounting for real AI calls, migration mismatch serving,
PostgreSQL incompatibility, secret leakage, unsafe production 500 responses,
Docker startup failure, backup artifacts in source/image, or full-suite
regression.

Final Task 300 recommendation:

```text
BACKEND READY FOR FRONTEND
```

Do not describe the full SaaS as commercially launch-ready until frontend,
billing, and deployment infrastructure are complete.

## Known Limitations

- The rate limiter is process-local and memory-backed. Run one FastAPI process
  for deterministic enforcement until a future distributed backend exists.
- There is no Redis, multi-region deployment, automatic failover, or
  Kubernetes/Terraform layer in this block.
- Backup/restore uses a logical baseline. Point-in-time recovery, WAL
  archiving, managed snapshots, retention, encryption operations, and RPO/RTO
  guarantees belong to deployment infrastructure.
- FastAPI does not own Meta WhatsApp Cloud access tokens; n8n runtime owns
  transport credentials.
- Live tunnels, provider app configuration, phone number availability, and
  external WhatsApp/Meta/n8n uptime are deployment prerequisites.
- Frontend and billing/subscriptions are not implemented yet.

## Phase A - Local Production Backend E2E

Status: PASSED.

Use disposable resources and placeholder secrets only. Do not call Meta, n8n,
Cloudflare, or a live LLM.

```powershell
docker network create task300-net

docker run --rm -d --name task300-postgres --network task300-net `
  -e POSTGRES_USER=task300 `
  -e POSTGRES_PASSWORD=<temporary-postgres-password> `
  -e POSTGRES_DB=task300 postgres:16-alpine

$env:DATABASE_URL = "postgresql+psycopg://task300:<temporary-postgres-password>@127.0.0.1:5432/task300"
$env:APP_ENV = "production"
$env:AUTH_TOKEN_SECRET = "<temporary-strong-auth-secret>"
$env:OUTBOUND_WEBHOOK_SIGNING_ENABLED = "true"
$env:OUTBOUND_WEBHOOK_URL = ""

alembic upgrade head
python -m app.migration_state check
docker build -t smart-sales-agency:task300 .

docker run --rm -d --name task300-api --network task300-net -p 8000:8000 `
  -e APP_ENV=production `
  -e AUTH_TOKEN_SECRET=<temporary-strong-auth-secret> `
  -e DATABASE_URL=postgresql+psycopg://task300:<temporary-postgres-password>@task300-postgres:5432/task300 `
  -e API_DOCS_ENABLED=false `
  -e METRICS_ENABLED=true `
  -e OUTBOUND_WEBHOOK_SIGNING_ENABLED=true `
  -e OUTBOUND_WEBHOOK_URL= `
  smart-sales-agency:task300

curl.exe -i http://127.0.0.1:8000/health
curl.exe -i http://127.0.0.1:8000/docs
curl.exe -i http://127.0.0.1:8000/metrics
```

Expected local probes:

- `GET /health` returns 200
- `GET /docs` returns 404
- `GET /metrics` returns 200 when metrics are enabled

Then use authenticated local API calls to verify:

1. create/authenticate an operator
2. create a workspace and confirm owner membership
3. create product and lead data
4. configure an integration account with a placeholder integration secret
5. send a signed local inbound integration request
6. confirm conversation, AI usage/accounting, and approval persistence
7. confirm outbound delivery does not run before approval
8. approve and use a stubbed/local outbound adapter boundary only
9. ingest sent/delivered/read provider-status callbacks with synthetic IDs
10. replay the inbound and callback events and confirm idempotency
11. verify workspace isolation and representative 429 behavior
12. inspect `/metrics` for bounded labels only
13. stop the API and PostgreSQL containers cleanly

Cleanup:

```powershell
docker stop task300-api
docker stop task300-postgres
docker network rm task300-net
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\APP_ENV -ErrorAction SilentlyContinue
Remove-Item Env:\AUTH_TOKEN_SECRET -ErrorAction SilentlyContinue
```

## Phase B - Real WhatsApp E2E

Status: PASSED.

Run this one step at a time after Phase A passes. Do not print or paste secrets,
phone numbers, bearer tokens, webhook signatures, provider delivery IDs, or live
customer content into source control or logs.

The completed manual acceptance verified, at a safe high level:

- real WhatsApp inbound delivery reached the Meta webhook
- Cloudflare and n8n forwarded the event successfully
- n8n verified the provider webhook signature
- n8n signed the provider-neutral inbound event for FastAPI
- FastAPI resolved the live workspace integration account and lead
- inbound processing returned HTTP 200
- Sales/AI produced a product-aware reply
- human approval was created and executed
- an outbound integration action was created and approved
- FastAPI's outbound HMAC contract was accepted by n8n
- n8n sent the message through the real WhatsApp Cloud API
- the target WhatsApp client received the message
- FastAPI persisted the safe delivery result
- provider status callbacks for sent, delivered, and read were persisted
- callback requests returned HTTP 200
- a deliberately unsupported but correctly signed outbound action reached
  application validation and failed safely as unsupported
- an expired provider token produced the expected provider-side OAuth failure,
  and replacing it with a fresh runtime-only token restored delivery

Preflight without exposing values:

1. Confirm FastAPI is running with production-like settings and current
   PostgreSQL schema.
2. Confirm n8n is running and has its runtime-only Meta WhatsApp credentials.
3. Confirm the public tunnel/webhook URL is current and reachable.
4. Confirm Meta callback configuration points to the current public webhook.
5. Confirm the workspace integration account is active and has a resolvable
   secret reference.
6. Confirm outbound signing/HMAC configuration is available in FastAPI and n8n.
7. If the tunnel expired, start a new tunnel and update Meta manually.

Manual live flow:

1. Send a new real client WhatsApp message.
2. Confirm Meta and n8n receive the inbound webhook.
3. Confirm FastAPI persists the inbound receipt and conversation turn.
4. Confirm workspace/account resolution is server-side and correct.
5. Confirm the Sales/AI path generates the proposed reply.
6. Confirm AI usage/accounting is persisted for the turn when real AI is used.
7. Confirm the current approval policy is obeyed.
8. If approval is required, approve through the authenticated operator API.
9. Confirm outbound action delivery runs through n8n and Meta.
10. Confirm the real client phone receives the reply.
11. Confirm sent, delivered, and read callbacks are received.
12. Confirm persisted callback ordering/history is sent -> delivered -> read.
13. Replay the inbound event and one provider callback and confirm no duplicate
    conversation, AI usage, outbound action, attempt, or status row.

This proves the external path:

```text
real client WhatsApp -> Meta -> n8n -> FastAPI integration boundary
-> workspace/account resolution -> Sales conversation/AI -> approval
-> outbound action -> n8n -> Meta -> real WhatsApp client
-> sent/delivered/read status history
```
