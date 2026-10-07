# Stage 3 local synthetic catalog

This directory documents a deterministic local-only catalog. It contains no real
company, person, case, article, or external-source data. Every generated record
uses the fixed investigation baseline date **2026-09-11**.

`tracerisk.synthetic.generate_catalog()` always returns these ten scenarios in
order:

1. `existing-judicial` — preserved 华辰智造 judicial baseline with two open cases.
2. `clean-operations` — four available sources and no risk rule match.
3. `all-rules-95` — the four deterministic rules total 95 points.
4. `alias-homonym` — a historical alias and a company-name homonym.
5. `duplicate-news` — two reports with one canonical news-story key.
6. `cross-source-conflict` — conflicting normalized judicial states by source.
7. `unavailable-source` — unavailable business source means evidence is insufficient.
8. `news-window-boundaries` — news at D-365, D-364, D, and D+1.
9. `business-date-boundaries` — resolved and future business-status boundaries.
10. `prompt-injection` — untrusted instruction-like text stored only as evidence content.

Company, alias, evidence, and coverage IDs use UUID5 values from fixed semantic
keys. The preserved Task 2.4 company and its three judicial evidence IDs are
declared exceptions and retain their exact existing values. Every evidence
content hash is the UTF-8 SHA-256 digest of its content. Catalog construction
uses only a private fixed-seed random generator, so global random consumption
cannot affect a result.

Prompt-injection-looking strings are opaque evidence content. They are never
used as catalog controls, fact types, fact keys, fact values, source fields, or
workflow instructions.

Inspect the pure catalog without database access with:

```powershell
$env:UV_CACHE_DIR='C:/Project/TraceRisk/.uv-cache'
uv run --directory apps/api python -c "from tracerisk.synthetic import generate_catalog; print([item.key for item in generate_catalog()])"
```

After applying Alembic migrations, import and verify the catalog with:

```powershell
$env:UV_CACHE_DIR='C:/Project/TraceRisk/.uv-cache'
uv run --project apps/api python scripts/seed_stage3.py
```

The command owns one database transaction. Existing matching stable rows are
preserved, mismatched rows abort the transaction, and a replay reports zero
inserted rows.

For a preserved local database, upgrade the business schema before importing
and never re-run Procrastinate schema setup:

```powershell
$env:UV_CACHE_DIR='C:/Project/TraceRisk/.uv-cache'
uv run --directory apps/api alembic upgrade head
uv run --directory apps/api alembic current
uv run --directory apps/api alembic check
uv run --project apps/api python scripts/seed_stage3.py
uv run --project apps/api python scripts/seed_stage3.py
```

The importer verifies 10 companies, 1 alias, 25 Evidence rows, and 40 coverage
rows. It also verifies every content hash and the preserved Task 2.4 company
and Evidence IDs. Once the catalog is already present, both imports report zero
insertions.

To exercise the catalog through the actual local API, Outbox dispatcher, and
worker, seed the demo accounts and start these commands in separate terminals:

```powershell
uv run --directory apps/api python -m tracerisk.auth.seed
uv run --directory apps/api python -m tracerisk
uv run --directory apps/api python -m tracerisk.workflows.outbox
uv run --directory apps/api python -m tracerisk.workflows.worker
```

Create investigations with `as_of_date=2026-09-11`. Only completed or failed
current Runs may be rerun. Coverage/conflict review changes routing only; rule
scores remain the deterministic 0–95 projection. Reports and prior Runs are
immutable snapshots, and the prompt-injection scenario remains untrusted data.
The local runtime uses synthetic accounts and data only and must not call
external sources or models.
