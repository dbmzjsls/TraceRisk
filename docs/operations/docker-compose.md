# Local Docker Compose lifecycle

These commands run the four local Mock/Fake services. Set a private `JWT_SECRET`
of at least 32 characters in a local `.env` file or shell environment before
running Compose. Keep `.env` out of source packages and screenshots.

The current Git, hosted CI, fresh-clone Docker, and rehearsal conclusions live in
the single [acceptance status](../acceptance.md). Historical Stage 6 records are
not startup authority. Keep the retained database and volume boundaries below
for every local run.

## First run, on either a fresh or retained volume

Run from the repository root:

```powershell
docker compose up -d --wait --wait-timeout 120 postgres
docker compose build api web
docker compose run --rm --no-deps api python scripts/database_lifecycle.py verify-retained
docker compose run --rm --no-deps api python scripts/database_lifecycle.py bootstrap-demo
docker compose run --rm --no-deps api python scripts/database_lifecycle.py verify-target --target tracerisk_demo
docker compose up -d --wait --wait-timeout 180 api worker web
docker compose ps
```

`verify-retained` reports `absent` on a fresh volume, or confirms the existing
`tracerisk` BGE index through read-only queries. `bootstrap-demo` explicitly
creates or finishes only `tracerisk_demo`, checks the connected database before
writes, and applies Alembic, queue, checkpoint, account, catalog, and Fake-index
initialization. It is safe to retry after partial setup. The runtime services
never run these steps on startup.

## Daily start

```powershell
docker compose up -d --wait --wait-timeout 120 postgres
docker compose run --rm --no-deps api python scripts/database_lifecycle.py verify-retained
docker compose run --rm --no-deps api python scripts/database_lifecycle.py verify-target --target tracerisk_demo
docker compose up -d --wait --wait-timeout 180 api worker web
docker compose ps
```

The daily path verifies the fully initialized target and starts it without
seeding or rebuilding its index. `POSTGRES_INIT_DB` controls only the PostgreSQL
server's first initialization; `APP_POSTGRES_DB` controls the API and worker
target and defaults to `tracerisk_demo`.

## Disposable acceptance target

Use `scripts/database_lifecycle.py bootstrap-e2e --run-id <32-lowercase-hex>
--record <owned-record-path>` with Mock/Fake environment and the host PostgreSQL
binding. The helper creates, records ownership of, and initializes only that
generated `tracerisk_e2e_*` database. Set `APP_POSTGRES_DB` to the exact returned
name before starting `api`, `worker`, and `web` with
`docker compose up -d --wait --wait-timeout 180 api worker web`; all acceptance
clients must use that same target. After testing, stop only application
services, close target connections, and run
`drop-owned --record <owned-record-path>`. That command
rejects any record that does not name the run's exact generated E2E database.
Finally restore `APP_POSTGRES_DB=tracerisk_demo`, verify it, and restart the
normal application services.

Keep the named `postgres_data` volume throughout. Never reset the retained
`tracerisk` database or rebuild its BGE index as part of Docker startup.
