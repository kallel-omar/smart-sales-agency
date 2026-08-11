# Production Runtime

Task 295 defines the supported FastAPI runtime policy. This is startup and
process hardening only; it does not add migrations, cloud deployment, external
monitoring, Redis, or provider network probes.

Task 296 adds the production packaging and operations contract for the FastAPI
runtime. It still does not add PostgreSQL migrations, backups, Kubernetes,
Terraform, external monitoring vendors, or production database conversion.

## Required Production Settings

Set production mode explicitly:

```sh
APP_ENV=production
AUTH_TOKEN_SECRET=<strong-random-secret>
```

`AUTH_TOKEN_SECRET` must be present, at least 32 characters, and not a known
development placeholder. Startup errors name the unsafe setting but never print
the value.

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
  -e API_DOCS_ENABLED=false \
  -e CORS_ALLOWED_ORIGINS=https://app.example.com \
  -e CORS_ALLOW_CREDENTIALS=false \
  -e METRICS_ENABLED=true \
  -e DATABASE_URL=sqlite:////app/data/sales_agency.db \
  -e OUTBOUND_WEBHOOK_SIGNING_ENABLED=true \
  smart-sales-agency-api:local
```

The image runs as an unprivileged `app` user. The default SQLite path inside the
image is `/app/data/sales_agency.db`, which is writable by that user. Mount a
volume at `/app/data` if SQLite state must survive container replacement.

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
only and performs no external network calls. The FastAPI lifespan then creates
configured database tables as before. No background workers are introduced by
Task 295.

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
local SQLite database files. Startup still uses SQLModel `create_all` for the
current schema behavior. PostgreSQL migrations, conversion, backup, restore, and recovery are deferred to Task 297 and later.
