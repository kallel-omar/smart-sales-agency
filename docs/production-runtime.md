# Production Runtime

Task 295 defines the supported FastAPI runtime policy. This is startup and
process hardening only; it does not add migrations, cloud deployment, external
monitoring, Redis, or provider network probes.

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

## Rate-Limit Runtime Limitation

The current Task 293 rate limiter is process-local and memory-backed. It gives
single-process guarantees only. Do not treat multiple Uvicorn workers as a
globally consistent rate-limit deployment until a shared backend, such as Redis,
is added in a later task.

## Health And Metrics

`GET /health` remains a lightweight liveness endpoint. It does not call Meta,
n8n, Cloudflare, databases beyond normal process state, or LLM providers.

`METRICS_ENABLED=true` exposes `/metrics` with the bounded labels from Task 292.
Network-level protection for metrics is a deployment concern for a later task.

## Startup Behavior

Startup validation is deterministic and local. It checks runtime configuration
only and performs no external network calls. The FastAPI lifespan then creates
configured database tables as before. No background workers are introduced by
Task 295.
