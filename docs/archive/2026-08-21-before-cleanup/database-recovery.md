# PostgreSQL Backup And Recovery

Task 299 defines the baseline production database recovery procedure for Smart
Sales Agency. PostgreSQL remains the database authority; FastAPI does not expose
backup or restore API endpoints and does not run backups from the web process.

## Scope

This runbook covers logical backup and restore with PostgreSQL-native tools:

- `pg_dump --format=custom --no-owner --no-acl`
- `pg_restore --exit-on-error --single-transaction --no-owner --no-acl`
- SHA-256 checksum verification
- Alembic schema verification after restore
- production startup against a restored database

It does not implement replication, automatic failover, WAL archiving,
point-in-time recovery, managed provider snapshots, or cross-region recovery.
RPO and RTO depend on the deployment's backup frequency, storage durability,
provider features, and operator process.

## Backup Data Classification

Database backups are sensitive production data. They may contain users,
customers, conversations, leads, products, integration configuration, approval
history, and operational audit rows.

Backup archives must not be committed to Git, copied into the FastAPI image,
uploaded as public CI artifacts, printed, or treated as harmless temporary
files. Production backup storage must be encrypted at rest by the deployment,
storage provider, or key-management layer. Task 299 does not add custom
encryption code.

The repository ignores common backup artifacts:

- `backups/`
- `*.dump`
- `*.backup`
- `*.dump.sha256`
- `*.backup.sha256`

## Credential Safety

Supply PostgreSQL connection information through runtime environment or the
deployment secret manager. Do not commit real DSNs, passwords, `.pgpass` files,
or generated backup archives.

Prefer environment-based invocation:

```powershell
$env:PGHOST = "<postgres-host>"
$env:PGPORT = "5432"
$env:PGDATABASE = "<database>"
$env:PGUSER = "<backup-user>"
$env:PGPASSWORD = "<runtime-secret>"
```

Do not echo `PGPASSWORD`, full `DATABASE_URL`, or provider credentials into logs.

## Backup Procedure

Create the archive outside the repository or under an ignored backup directory:

```powershell
New-Item -ItemType Directory -Force .\backups | Out-Null
pg_dump --format=custom --no-owner --no-acl --file .\backups\smart-sales-agency.dump
Get-FileHash .\backups\smart-sales-agency.dump -Algorithm SHA256 |
  Set-Content .\backups\smart-sales-agency.dump.sha256
```

Verify the command exits zero, the archive exists, the archive is non-empty, and
the checksum is recorded. The optional application helper in
`app.database_backup` records only safe artifact metadata: format, timestamp,
application migration revision, archive filename, size, and SHA-256.

## Restore Procedure

Restore into a freshly provisioned empty database. Do not restore over the live
source database as the normal flow, and do not use destructive `--clean` against
production unless an operator has deliberately selected that recovery path.

```powershell
$env:PGDATABASE = "<fresh-restore-database>"
pg_restore --exit-on-error --single-transaction --no-owner --no-acl `
  --dbname $env:PGDATABASE .\backups\smart-sales-agency.dump
```

After restore:

1. Verify the backup checksum again.
2. Run `python -m app.migration_state check` with `DATABASE_URL` pointed at the
   restored database.
3. If the restored database is behind a future application version, run
   `alembic upgrade head`, then run the migration check again.
4. Validate representative restored data and relational constraints.
5. Start FastAPI only when the migration check reports current.

Current Task 299 same-version recovery expects the restored database to already
be at the application Alembic head.

## Startup Resilience

Production startup still fails closed unless the database schema is current.
Task 299 adds bounded retry only for transient schema inspection failures:

- `DATABASE_STARTUP_MAX_ATTEMPTS=3`
- `DATABASE_STARTUP_RETRY_DELAY_SECONDS=1`

Only `check_failed` can retry. Known migration states fail immediately:

- uninitialized schema
- behind schema
- ahead or unknown schema
- multiple heads

Retry logs include bounded operational fields such as event name, attempt,
maximum attempts, delay, state, and exception type. They never log
`DATABASE_URL`, database passwords, raw exception messages, or host/user
credential pairs.

## Request-Time Failure Semantics

Task 299 does not add transparent retry for business writes. A PostgreSQL write
may commit before the application sees a connection failure, so automatically
repeating mutations could duplicate approvals, conversations, outbound actions,
integration receipts, provider callbacks, user creation, or AI accounting.

Stale pooled PostgreSQL connections are handled with SQLAlchemy `pool_pre_ping`.
Failed request-scoped sessions are rolled back and closed by the shared FastAPI
session dependency. Unexpected request-time database errors still flow through
Task 294 safe generic 500 handling with request IDs, structured completion logs,
and normal HTTP metrics.

`GET /health` remains local process liveness. It does not probe PostgreSQL,
Meta, n8n, Cloudflare, or LLM providers on every request.

## Manual Disposable Acceptance

Use disposable databases and test-only values. Do not use live databases,
WhatsApp, Meta, n8n, or customer data.

The expected acceptance flow is:

1. Create a disposable Docker network.
2. Start a source PostgreSQL container.
3. Run `alembic upgrade head` against the source database.
4. Seed deterministic fake application data.
5. Create a custom-format `pg_dump` archive outside tracked source.
6. Generate and verify a SHA-256 checksum.
7. Create a separate fresh restore database or restore container.
8. Restore the archive with `pg_restore --single-transaction`.
9. Run `python -m app.migration_state check` against the restored database.
10. Query representative restored data.
11. Start production FastAPI against the restored database.
12. Verify `/health` returns 200.
13. Optionally stop/remove the source and confirm the restored target remains
    independent.
14. Stop FastAPI gracefully.
15. Remove containers, network, backup archive, and checksum file.

Point-in-time recovery and retention policy are deployment-owned concerns for a
future operations layer.
