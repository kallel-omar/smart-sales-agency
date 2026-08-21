# Production Runtime

Task 295 defines the supported FastAPI runtime policy. This is startup and
process hardening only; it does not add migrations, cloud deployment, external
monitoring, Redis, or provider network probes.

Task 296 adds the production packaging and operations contract for the FastAPI
runtime. Task 297 adds the PostgreSQL persistence and migration foundation.
Task 298 adds production migration lifecycle safety: deployments must migrate
the database explicitly, and FastAPI refuses to serve production traffic unless
the database is at the application's expected Alembic head. Task 299 adds the
baseline PostgreSQL logical backup and restore runbook plus bounded startup
retry for transient database inspection failures. This still does not add
Kubernetes, Terraform, external monitoring vendors, replication, failover, or
point-in-time recovery infrastructure.

## Required Production Settings

Set production mode explicitly:

```sh
APP_ENV=production
AUTH_TOKEN_SECRET=<strong-random-secret>
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/<database>
```

`AUTH_TOKEN_SECRET` must be present, at least 32 characters, and not a known
development placeholder. Startup errors name the unsafe setting but never print
the value.

PostgreSQL is required in production. SQLite remains available for development,
unit tests, and lightweight local work only. Production rejects SQLite instead
of silently treating it as final persistence.

Production startup may retry transient database schema inspection failures using
bounded settings:

```sh
DATABASE_STARTUP_MAX_ATTEMPTS=3
DATABASE_STARTUP_RETRY_DELAY_SECONDS=1
```

Only failed inspection/connectivity checks retry. Known migration mismatches
still fail immediately.

If `OUTBOUND_WEBHOOK_URL` is configured in production,
`OUTBOUND_WEBHOOK_SIGNING_ENABLED=true` is required. FastAPI still does not own
Meta WhatsApp Cloud access tokens; those stay in the n8n runtime.

## API Docs

Interactive docs are enabled by default in development and test. In production,
docs are disabled by default:

- `/docs`
- `/redoc`
- `/openapi.json`

Set `API_DOCS_ENABLED=true` only for a deliberately protected production
environment.

## CORS

CORS is static process configuration:

```sh
CORS_ALLOWED_ORIGINS=https://app.example.com
CORS_ALLOW_CREDENTIALS=false
```

Leave `CORS_ALLOWED_ORIGINS` empty to disable browser cross-origin access.
Production rejects wildcard origins. Wildcard origins are always rejected when
credentials are enabled. Origin values must be exact `http` or `https` origins,
with no paths, queries, fragments, or embedded credentials.

## Trusted Proxies

The application does not parse or trust arbitrary forwarded headers. In
particular, request logic must not automatically trust:

- `X-Forwarded-For`
- `X-Forwarded-Proto`
- `X-Forwarded-Host`
- `CF-Connecting-IP`

If a deployment sits behind a known reverse proxy, configure trust at the
Uvicorn/server boundary with explicit proxy IPs. Do not use trust-all proxy
configuration in production.

## Server Command

Use a normal Uvicorn process without reload:

```sh
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers
```

If a trusted proxy is required, replace `--no-proxy-headers` with explicit
Uvicorn proxy settings for that proxy only.

Do not use `--reload` in production.

The production Dockerfile uses this command directly and does not configure
multiple workers. Startup configuration failures exit the process non-zero
instead of falling back to development behavior.

## Production Image

Build the FastAPI image from the repository root:

```sh
docker build -t smart-sales-agency-api:local .
```

Run it with production settings supplied at container runtime:

```sh
docker run --rm --name smart-sales-agency-api -p 8000:8000 \
  -e APP_ENV=production \
  -e AUTH_TOKEN_SECRET=<strong-random-secret> \
  -e DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/<database> \
  -e API_DOCS_ENABLED=false \
  -e CORS_ALLOWED_ORIGINS=https://app.example.com \
  -e CORS_ALLOW_CREDENTIALS=false \
  -e METRICS_ENABLED=true \
  -e OUTBOUND_WEBHOOK_SIGNING_ENABLED=true \
  smart-sales-agency-api:local
```

The image runs as an unprivileged `app` user. It contains the application,
PostgreSQL driver, Alembic runtime, and migration scripts. It does not run or
embed PostgreSQL.

The build context excludes local `.env` files, n8n `.env` files, `.git`,
virtual environments, Python/test caches, logs, and local database files. Do
not bake live credentials into the image.

## Healthcheck

The container healthcheck calls only:

```text
GET http://127.0.0.1:8000/health
```

It uses Python from the image, has bounded timeout/retry settings, and never
checks Meta, n8n, Cloudflare, LLM providers, or `/metrics`.

## Rate-Limit Runtime Limitation

The current Task 293 rate limiter is process-local and memory-backed. It gives
single-process guarantees only. Do not treat multiple Uvicorn workers as a
globally consistent rate-limit deployment until a shared backend, such as Redis,
is added in a later task.

Run one FastAPI process/worker for now. Multiple containers or workers would each have independent buckets.

## Health And Metrics

`GET /health` remains a lightweight liveness endpoint. It does not call Meta,
n8n, Cloudflare, databases beyond normal process state, or LLM providers.

`METRICS_ENABLED=true` exposes `/metrics` with the bounded labels from Task 292.
Network-level protection for metrics is a deployment concern for a later task.
Metrics contain application-level counters and histograms only; they must not be
used for tenant, customer, request ID, or secret-bearing labels.

## Startup Behavior

Startup validation is deterministic and local. It checks runtime configuration
and production database schema state, and performs no external provider network
calls. In development and test, the FastAPI lifespan may create configured
database tables for local convenience. In production, Alembic migrations are the
schema authority; application startup does not run `create_all` or automatically
upgrade the database. No background workers are introduced by Tasks 295-299.

Run migrations as an explicit release step after configuring `DATABASE_URL` and
before starting the application:

```sh
alembic upgrade head
```

The migration configuration reads `DATABASE_URL` from the same application
settings at runtime. Do not hard-code database credentials into Alembic files,
Docker images, or source control.

## Migration Lifecycle

The production deployment sequence is:

1. Provision and reach PostgreSQL.
2. Set `DATABASE_URL` securely in the runtime environment.
3. Run `alembic upgrade head`.
4. Optionally run `python -m app.migration_state check`.
5. Start FastAPI.
6. FastAPI independently verifies that the database is at the application
   Alembic head before accepting traffic.

The precheck command exits `0` only when the configured database is current:

```sh
python -m app.migration_state check
```

It exits non-zero for uninitialized, outdated, unknown/ahead, multiple-head, or
failed schema checks. Output is intentionally concise and never prints
credential-bearing database URLs.

Production FastAPI startup refuses unsafe schema states:

- uninitialized database
- database behind the application head
- database ahead of or unknown to the application
- multiple database current heads
- database connection or schema-check failure

Startup refusal is intentional. Operators must run the migration step before the
web runtime starts. FastAPI never runs `alembic upgrade head` automatically.

If PostgreSQL is briefly unavailable while the container starts, FastAPI retries
only the schema inspection according to the bounded `DATABASE_STARTUP_*`
settings. It does not retry business writes or mutate domain state during
startup recovery.

Migration history is forward-only for production. Do not edit an already applied
committed revision. Create a new migration for schema changes, review generated
migrations before deployment, run migrations before the new app version, and let
startup verification block mismatches. Do not rely on `create_all` for
production.

Automatic production downgrade is not a recovery strategy. Application rollback
must consider database compatibility. Backup and recovery work belongs to Task
299.

## Shutdown

The production container runs Uvicorn as the single application process. Normal
container stop sends a termination signal to Uvicorn, which stops accepting new
traffic, runs FastAPI shutdown, and exits cleanly. Task 296 does not add
background workers or long-running side processes.

## Environment Ownership

FastAPI-owned settings include:

- `APP_ENV`
- `AUTH_TOKEN_SECRET`
- `API_DOCS_ENABLED`
- `CORS_ALLOWED_ORIGINS`
- `CORS_ALLOW_CREDENTIALS`
- `METRICS_ENABLED`
- `DATABASE_URL`
- `DATABASE_STARTUP_MAX_ATTEMPTS`
- `DATABASE_STARTUP_RETRY_DELAY_SECONDS`
- `OUTBOUND_WEBHOOK_URL`
- `OUTBOUND_WEBHOOK_SIGNING_ENABLED`
- logging, AI boundary, integration readiness, and rate-limit settings

n8n-owned settings include Meta transport credentials such as:

- `WHATSAPP_CLOUD_ACCESS_TOKEN`
- WhatsApp Cloud phone-number and Graph API transport configuration

FastAPI does not require or own `WHATSAPP_CLOUD_ACCESS_TOKEN`.

## Monitoring

The current operations baseline is provider-neutral:

- structured JSON logs with request IDs from Task 291
- Prometheus metrics from Task 292
- safe generic client error responses from Task 294

No Sentry, OpenTelemetry exporter, or vendor SDK is required for core startup.
If an external monitoring integration is added later, it must send only bounded
operational metadata and never request bodies, auth headers, cookies, passwords,
tokens, customer content, phone numbers, emails, tenant IDs, or provider IDs.

## Database Boundary

`DATABASE_URL` remains runtime configuration. The production image does not copy
local SQLite database files. PostgreSQL availability is deployment
infrastructure responsibility; the FastAPI container does not include a database
server. Logical backup and restore use PostgreSQL-native tooling outside the
FastAPI process:

- `pg_dump --format=custom --no-owner --no-acl`
- `pg_restore --exit-on-error --single-transaction --no-owner --no-acl`

Backup archives are sensitive production data and must be encrypted at rest by
the deployment/storage layer. They must not be committed to Git, copied into the
FastAPI image, or uploaded as public CI artifacts. See
`docs/database-recovery.md` for the Task 299 recovery runbook.

Point-in-time recovery, WAL archiving, managed snapshots, archival retention,
and advanced recovery orchestration remain deployment-owned concerns for later
tasks.
