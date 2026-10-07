# Stage 4 retrieval and generation acceptance

Date: 2026-09-18. Scope: approved Stage 4; Stage 5 remains unchecked.

## Retrieval results

| Domain | Cases | Fake Recall@5 | CPU BGE Recall@5 |
| --- | ---: | ---: | ---: |
| business | 4 | 1.000000 | 1.000000 |
| judicial | 4 | 1.000000 | 1.000000 |
| news | 6 | 1.000000 | 1.000000 |
| intellectual_property | 2 | 1.000000 | 1.000000 |
| Overall, mean over cases | 16 | **1.000000** | **1.000000** |

Recall@5 is the number of distinct labeled Evidence IDs found among the first
five retrieved Evidence IDs divided by the relevant set size, then averaged
over cases. Domain averages are reported independently; the overall average
weights cases, not domains. Duplicate reports retain distinct Evidence IDs;
the rule layer's story-key deduplication remains a separate operation.

The source catalog has only 25 Evidence rows, with at most five eligible items
in any evaluated company/domain scope. These are ceiling/smoke results, not
evidence of useful ranking discrimination or real-world accuracy. Fake is a
deterministic regression baseline and must not be interpreted as BGE quality.
No synthetic distractors or production evidence were added to inflate the test.

The static JSONL labels were explicitly selected from inspected synthetic
source events with a rationale for every case, before inspecting ranked
results. They cover four risks, aliases/homonyms, duplicate notices/news,
opposing sources, inclusive/exclusive dates, noncore IP, and injected content.
They are source-grounded acceptance labels, not an independently blinded
business-expert benchmark. The one-time authoring record is
`.superpowers/sdd/stage-4-label-authoring.py`.

The missing-source company has no Evidence at all. Its four-domain empty
retrieval is a separate asserted `scope_probes` entry in both reports, excluded
from Recall because an empty relevance denominator is undefined. The loader
rejects empty relevant sets, duplicate/unsorted case IDs, duplicate/unsorted
labels/tags, unknown companies/risks/Evidence, blank text, extra fields, and
labels outside the company/domain/date scope.

## Reproducibility and isolation

- Dataset: `datasets/evaluation/retrieval_cases.jsonl`.
- Dataset SHA-256: `216977e6ba49ccfb7595f786db28cc8bb794ec6f02a3d760097cf2e0617edd8f`.
- Source snapshot SHA-256: `245aa229bab915c9b423ff7c14c5c3cc9eec36f6e23ac00da4069bf9bf5f2f40`.
- Fake DB: `tracerisk_eval_stage4_fake`; BGE DB: `tracerisk_eval_stage4_bge`.
- Both databases were separately created, migrated to `0010`, seeded by
  `scripts/seed_stage3.py`, and indexed through the explicit index CLI.
- Both use the same scoped keyword + pgvector cosine + RRF(k=60) + Evidence
  version deduplication + Top-5 implementation as normal workflow retrieval.
- Evaluation verifies all catalog fields through the conflict-checking importer
  and rejects missing/extra source rows before embedding case-specific queries.
- Fake ran twice after the final evaluation implementation and produced
  byte-identical reports, SHA-256
  `68942d7ce3c43c4745c6469eee0ce6b6e25559cc7ff4165b8d59536d17495137`.
- BGE report SHA-256:
  `0865a4fe30780cf2893da2d8cb24bd3666f6280b4373f2dde5ef1e4090114ee6`.

Machine reports: `stage-4-fake.json`, `stage-4-fake-repeat.json`,
`stage-4-bge.json`, and `stage-4-environment.json` in this directory. Reports
contain case-level retrieved IDs and query hashes, domain/overall Recall,
dataset/source hashes, model/device/dimension/index/query metadata, and the
missing-source probe. Timestamps, database-local UUIDs, and latency are omitted
from deterministic evaluation reports.

## Actual BGE installation and execution

The explicit optional install was `uv sync --extra bge --frozen` in `apps/api`.
Default project dependencies and CI installation paths were not changed.
Installed versions: sentence-transformers 5.7.0, transformers 5.17.0,
torch 2.14.0, pgvector 0.5.0. Model cache:
`.cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5`, revision
`7999e1d3359715c523056ef9478215996d62a620`.

Real model: `BAAI/bge-small-zh-v1.5`, CPU, normalized 512-dimensional vectors,
cosine. Document inputs have no instruction prefix; case queries use the
adapter's fixed Chinese retrieval prefix. Model loading occurred only in the
explicit download smoke, index CLI, and evaluation process. After initial
download, indexing/evaluation ran against the local cache with Hub offline.

To reproduce on a separately provisioned database, set `PYTHONPATH` to
`apps/api/src`, `POSTGRES_DB` to the appropriate `tracerisk_eval_...` database,
and `HF_HOME` to the workspace cache for BGE. Then run from the repository root:

```text
apps/api/.venv/Scripts/python.exe -m tracerisk.retrieval.index_cli --provider fake
apps/api/.venv/Scripts/python.exe scripts/evaluate_retrieval.py --provider fake --output docs/evaluation/stage-4-fake.json
apps/api/.venv/Scripts/python.exe -m tracerisk.retrieval.index_cli --provider bge
apps/api/.venv/Scripts/python.exe scripts/evaluate_retrieval.py --provider bge --output docs/evaluation/stage-4-bge.json
```

Use separate DB environment values for the Fake and BGE command pairs. The
evaluation script refuses the preserved application's database name. A different
active provider cannot be silently relabeled or replaced by the index command.

## Preserved application and model-free runtime

The application database remains on
`bge:BAAI/bge-small-zh-v1.5:512:cosine:v1`, with 25 document vectors, four fixed
`stage4-risk-queries-v1` vectors, and 37 chunks (12 preserved legacy + 25 v1).
The named volume was retained. Historical row hashes captured before indexing
remain present for all 15 inspected business tables, including every previous
report, citation, rule finding, review, and audit row. Six Stage 3 manifests and
five new Stage 4 manifests replay; four older pre-manifest runs remain readable
as preserved rows. See `stage-4-preservation.json` for exact counts.

The explicit BGE index CLI was replayed with sentence-transformers and torch
blocked. It succeeded without embedding and left all row counts unchanged;
`stage-4-index-replay.json` records PostgreSQL 17.11, vector 0.8.6, revision
`0010`, and before/after counts.

Separate normal API, dispatcher, and worker processes installed import blockers
for sentence-transformers, torch, and transformers before application imports,
enabled offline flags, and allowed only loopback network connections. No blocked
import/network attempt occurred. The 95-point investigation retrieved business
1, judicial 1, news 2, and IP 2 evidence items using stored query vectors.

The local OpenAI-compatible simulator ran five workflow scenarios: success,
503 then success and human review, terminal 401, untrusted prompt injection,
and completed-run rerun. There were six transport attempts and six durable
invocation rows, four successes and two failures; each success recorded 17 input
and 9 output tokens. The previous report remained unchanged after rerun. No
external generation model was called. Invocation evidence contains no key,
authorization value, full prompt, or response body.

Runtime evidence: `stage-4-runtime-result.json` and
`.superpowers/sdd/stage-4-runtime-acceptance-report.md`. The reusable harness is
`.superpowers/sdd/stage-4-runtime-acceptance.py`.

Supplementary `stage-4-runtime-readiness.json` proves the real BGE evaluation
index and the Fake index each reject missing vectors, wrong query version,
wrong query hash, and incompatible index metadata before ranking. All eight
actual worker-task paths became terminal with one failure audit, zero retrieval
rows, and zero reports. Each mutation was rolled back; source/index digests and
counts stayed unchanged. BGE imports and non-loopback networking were blocked.

## Validation

The pending Task 1 P3 regression now explicitly builds canonical paragraph
chunks, attempts downgrade to `0009`, verifies refusal, and confirms revision
`0010`, both chunks/vectors, and index readiness remain intact.

Focused evaluation tests: **20 passed**. The complete backend suite is invoked
exactly once by `.superpowers/sdd/stage-4-validation.py`; final results are in
`stage-4-validation.json` and `.superpowers/sdd/stage-4-validation.log`.
Those artifacts also record Ruff, format, strict mypy, Alembic upgrade/current/
drift, generated API contract consistency, and preserved-database replay.

| Check | Result |
| --- | --- |
| Single complete backend invocation | 290 passed, 2 stale E2E expectations failed, 349.46 s |
| Targeted corrected E2E rerun | 2 passed, 16.88 s |
| Ruff / format | Passed / 137 files formatted; changed E2E file rechecked |
| Strict mypy | Passed, 83 source files, no incremental cache |
| Alembic | Upgrade replay passed; `0010 (head)`; no drift |
| API contract | Generated types match local OpenAPI |
| Preserved database | All historical row digests retained; 11 manifests replayed |

The two stale Stage 2 tests expected free-form Mock prose to be directly
supported. One now asserts all three mandated review reasons; the direct-report
fixture now emits canonical supported facts. Production code was unchanged
after the complete invocation. The full suite was not repeated; its initial
failure output and the successful focused correction are both retained rather
than presenting the original run as green. A first focused correction caught a
test fixture's unavailable `source_type` field, fixed by its known judicial
source. No unresolved failures remain. One Starlette/AnyIO deprecation warning
remains. Captured ephemeral JWT fragments were redacted from failure artifacts.

The implementation checklist has four Stage 4 groups containing **16** existing
items (4 + 4 + 5 + 3), not 18. Only these existing items are marked complete;
Stage 5 remains unchanged. Task 6 acceptance is complete; final integrated
Stage 4 review status is maintained in `.superpowers/sdd/stage-4-progress.md`.
