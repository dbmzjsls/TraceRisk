# Real Model Challenge Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute a reproducible 40-case DeepSeek challenge evaluation for TraceRisk, make one evidence-led change, rerun the frozen dataset, and generate auditable results plus one truthful resume bullet.

**Architecture:** Add a database-free evaluation package that converts synthetic frozen chunks into the existing TraceRisk AI schemas and support checks, while a dedicated HTTP client records every real DeepSeek attempt. Persist append-only per-case JSONL first, derive all summaries and reports from those files, then use a deterministic attribution rule to authorize exactly one improvement dimension for the second run.

**Tech Stack:** Python 3.12, Pydantic 2, httpx, pytest, existing TraceRisk claim/support modules, DeepSeek OpenAI-compatible Chat Completions API.

---

## File map

Create these source files, each with one responsibility:

- `apps/api/src/tracerisk/challenge_eval/__init__.py`: package exports only.
- `apps/api/src/tracerisk/challenge_eval/schemas.py`: strict case, run, HTTP-attempt, result, review and summary models.
- `apps/api/src/tracerisk/challenge_eval/dataset.py`: JSONL loading, contract validation and stable hashes.
- `apps/api/src/tracerisk/challenge_eval/prompts.py`: baseline prompt builder and prompt hash.
- `apps/api/src/tracerisk/challenge_eval/client.py`: DeepSeek request, retry boundary, usage and safe response capture.
- `apps/api/src/tracerisk/challenge_eval/validation.py`: production-schema parsing, citation integrity, conflict/support checks and routing.
- `apps/api/src/tracerisk/challenge_eval/scoring.py`: per-case comparison, error classification, field diffs and cost calculation.
- `apps/api/src/tracerisk/challenge_eval/attribution.py`: mutually exclusive attribution, impact ranking and single-variable manifest validation.
- `apps/api/src/tracerisk/challenge_eval/reporting.py`: aggregation, Markdown, typical cases and resume bullet generation.
- `apps/api/src/tracerisk/challenge_eval/runner.py`: staged orchestration, append-only persistence, resume and interactive review.
- `scripts/run_model_challenge.py`: thin CLI wrapper.

Create these fixed inputs and tests:

- `datasets/evaluation/model_challenge_config.json`: model parameters, retry policy, pricing snapshot, impact weights and source links/date.
- `datasets/evaluation/model_challenge_cases.jsonl`: exactly 40 hand-curated synthetic cases.
- `apps/api/tests/challenge_eval/conftest.py`: small valid cases and recorded HTTP responses.
- `apps/api/tests/challenge_eval/test_dataset.py`
- `apps/api/tests/challenge_eval/test_client.py`
- `apps/api/tests/challenge_eval/test_validation.py`
- `apps/api/tests/challenge_eval/test_scoring.py`
- `apps/api/tests/challenge_eval/test_attribution.py`
- `apps/api/tests/challenge_eval/test_runner.py`
- `apps/api/tests/challenge_eval/test_reporting.py`

Modify documentation only after measured results exist:

- `README.md`: add the real-model evaluation command and distinguish it from CI Mock/Fake gates.
- `docs/resume-bullets.md`: append the generated, measured bullet and its limitations.

Generated, committed artifacts go under `docs/evaluation/model-challenge/`:

- `baseline/manifest.json`, `baseline/attempts.jsonl`, `baseline/case-events.jsonl`, `baseline/cases.jsonl`, `baseline/reviews.jsonl`, `baseline/summary.json`
- `analysis.json`
- `improved/manifest.json`, `improved/attempts.jsonl`, `improved/case-events.jsonl`, `improved/cases.jsonl`, `improved/reviews.jsonl`, `improved/summary.json`
- `connectivity-check.json`
- `comparison.md`, `typical-cases.md`, `resume-bullet.md`, `README.md`

The exact improvement source/config file is deliberately data-dependent. Task 8 permits one and only one of the following mutually exclusive behavior components after baseline attribution: the prompt builder in `prompts.py`, the validation-feedback builder in `validation.py`, the context-order function in `prompts.py`, or the review-routing function in `validation.py`. Dataset bytes never change. The manifest inventories `scripts/run_model_challenge.py` and recursively inventories the complete local `tracerisk` import graph rooted at `challenge_eval` by parsing imports and hashing every reachable `.py` file; this necessarily includes the production schemas, claims and validation modules. For the selected file, it also stores normalized AST digests for every top-level function, class and constant. The improved verifier requires every file and AST node to match except the one selected function node; changing the CLI entrypoint, a shared helper, transitive production helper, constant, import, decorator or second node is rejected. Do not implement unused alternative improvements.

### Task 1: Strict schemas and dataset contract

**Files:**
- Create: `apps/api/src/tracerisk/challenge_eval/__init__.py`
- Create: `apps/api/src/tracerisk/challenge_eval/schemas.py`
- Create: `apps/api/src/tracerisk/challenge_eval/dataset.py`
- Create: `apps/api/tests/challenge_eval/conftest.py`
- Create: `apps/api/tests/challenge_eval/test_dataset.py`

- [ ] **Step 1: Write failing dataset tests**

Define a minimal valid case fixture and test strict rejection for duplicate IDs, wrong content hashes, missing support/distractor IDs, overlapping support and distractor IDs, unknown claim codes, empty rationales, invalid actions and category imbalance. Test that the real dataset must contain exactly 40 cases and exactly 10 cases per category.

```python
def test_frozen_dataset_is_balanced_and_valid() -> None:
    loaded = load_cases(DATASET)
    assert len(loaded.cases) == 40
    assert Counter(case.category for case in loaded.cases) == {
        "explicit_evidence": 10,
        "similar_entity_or_number": 10,
        "source_conflict": 10,
        "insufficient_evidence": 10,
    }
    assert loaded.sha256 == sha256(DATASET.read_bytes()).hexdigest()
```

- [ ] **Step 2: Run the focused test and confirm failure**

Run:

```powershell
$env:PYTHONPATH = 'apps/api/src;.'
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval/test_dataset.py -q
```

Expected: collection or import failure because `tracerisk.challenge_eval` does not exist.

- [ ] **Step 3: Implement strict Pydantic models**

Use `ConfigDict(extra="forbid")` everywhere. Define deterministic UUID/string IDs, `Literal` categories/actions, `ChallengeChunk` fields matching production `EvidenceContext`, `ChallengeCase`, `AttemptRecord`, `CaseResult`, `ReviewRecord`, `RunManifest` and `RunSummary`. Make metric fields nullable when the API or human review did not provide them.

Required result distinction:

```python
ExpectedAction = Literal["accept", "reject", "manual_review"]
Attribution = Literal[
    "structure_error", "citation_error", "support_error", "human_judgment"
]

class CaseResult(StrictModel):
    case_id: str
    expected_action: ExpectedAction
    actual_action: ExpectedAction | None
    first_attempt_usable: bool
    attempts: list[AttemptRecord]
    failure_localization_ms: int | None
    failure_localization_status: Literal["measured", "interrupted_before_finalize"]
    human_modified_field_count: int | None = None
    review_duration_ms: int | None = None
    review_measurement_status: Literal["measured", "not_measured"]
```

- [ ] **Step 4: Implement JSONL loading and integrity checks**

Read UTF-8 one object per nonblank line, reject duplicate `case_id`/chunk IDs, recompute SHA-256 over exact chunk content, validate support/distractor references and require allowed claim codes to be keys in `tracerisk.ai.claims.CLAIM_CATALOG`. Return cases plus the exact file-byte hash.

- [ ] **Step 5: Run tests and static checks**

Run:

```powershell
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval/test_dataset.py -q
apps/api/.venv/Scripts/python.exe -m ruff check apps/api/src/tracerisk/challenge_eval apps/api/tests/challenge_eval
apps/api/.venv/Scripts/python.exe -m mypy apps/api/src/tracerisk/challenge_eval
```

Expected: focused tests and checks pass, except the real-dataset count test may remain xfailed only until Task 2. Remove that xfail in Task 2.

- [ ] **Step 6: Commit**

```powershell
git add apps/api/src/tracerisk/challenge_eval apps/api/tests/challenge_eval
git commit -m "test: define challenge evaluation contracts"
```

### Task 2: Freeze the 40 challenge cases and run configuration

**Files:**
- Create: `datasets/evaluation/model_challenge_cases.jsonl`
- Create: `datasets/evaluation/model_challenge_config.json`
- Modify: `apps/api/tests/challenge_eval/test_dataset.py`

- [ ] **Step 1: Add failing assertions for semantic coverage**

Require each category to cover all four production risk types at least twice, every similar case to contain at least one distractor, every conflict case to contain mutually exclusive values from different sources for one fact key at the same normalized `observed_at`, and every explicit case to expect `accept`. Add a negative fixture proving different observation dates are a legitimate status change rather than a conflict.

- [ ] **Step 2: Hand-author 40 cases**

Write exactly ten cases for each category. Vary names, case numbers, amounts, percentages, dates and source types. Every line must include the expected action, support chunks, distractor chunks, allowed claim codes and a concrete Chinese rationale. Use synthetic names and records only; do not derive cases from model output.

Use stable UUIDv5 values derived from `case_id/chunk ordinal` or fixed literal UUIDs, then store the real SHA-256 of every content string. Do not add a generator dependency.

- [ ] **Step 3: Freeze model and pricing configuration**

Store non-secret settings:

```json
{
  "provider": "deepseek",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-flash",
  "thinking": {"type": "disabled"},
  "temperature": 0,
  "max_tokens": 2000,
  "response_format": {"type": "json_object"},
  "max_attempts": 2,
  "pricing_snapshot_date": "2026-10-23",
  "pricing_currency": "USD",
  "price_per_million_tokens": {
    "peak_cache_hit_input": 0.006,
    "peak_cache_miss_input": 0.3,
    "peak_output": 1.2,
    "off_peak_cache_hit_input": 0.003,
    "off_peak_cache_miss_input": 0.15,
    "off_peak_output": 0.6
  }
}
```

Also store the official pricing URL and peak-hour rule. Select and record `pricing_tier` in each run manifest from the actual request timestamp; if a Chinese public holiday cannot be established from a checked-in calendar, use the higher peak rate and label the value `conservative_estimated_cost_usd`, not billed cost.

- [ ] **Step 4: Run the dataset tests**

Run:

```powershell
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval/test_dataset.py -q
```

Expected: all dataset contract and semantic coverage tests pass with no xfail.

- [ ] **Step 5: Commit**

```powershell
git add datasets/evaluation/model_challenge_cases.jsonl datasets/evaluation/model_challenge_config.json apps/api/tests/challenge_eval/test_dataset.py
git commit -m "test: add frozen model challenge dataset"
```

### Task 3: Baseline prompt, response parsing and production-boundary validation

**Files:**
- Create: `apps/api/src/tracerisk/challenge_eval/prompts.py`
- Create: `apps/api/src/tracerisk/challenge_eval/validation.py`
- Create: `apps/api/tests/challenge_eval/test_validation.py`

- [ ] **Step 1: Write failing validation and routing tests**

Cover: exact supported finding accepts; empty findings reject; outside-chunk citation rejects; hash/evidence/excerpt mismatch rejects; distractor selection becomes `citation_error`; unknown/wrong-domain claim becomes `support_error`; valid free-text extension routes to manual review; same-key, same-normalized-time cross-source conflict routes to manual review; the same fact key with different observation times does not conflict; a hard citation error cannot be laundered into manual review.

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval/test_validation.py -q
```

Expected: imports fail for the new prompt and validation modules.

- [ ] **Step 3: Build the intentionally minimal baseline prompt**

The baseline must identify evidence as untrusted, embed the existing `StructuredRiskFindings.model_json_schema()`, list allowed claim codes, require exact excerpts and state that unsupported conclusions should return `{"findings": []}`. It must not include hints tailored to the four challenge categories, entity/number disambiguation steps, conflict handling recipes or distractor labels.

Serialize each chunk with the same escaped `<untrusted_evidence>` boundary used by production. The prompt must never serialize `supporting_chunk_ids`, `distractor_chunk_ids`, expected action, category or rationale.

- [ ] **Step 4: Implement validation without a database**

Parse with `StructuredRiskFindings.model_validate_json(..., strict=True)`. Resolve citations only against the case's input chunks; verify evidence ownership, hash and continuous excerpt. Convert resolved chunks to `ClaimFact`, call `decide_claim_support`, and reuse `validate_draft_support` for canonical/open-text behavior.

Detect challenge conflict routing from input facts using the same-company, same-fact-key, equal normalized `observed_at`, different-source, mutually-exclusive-value rule. Keep hard errors dominant. Return a structured `ValidationOutcome` with `actual_action`, error type, cited IDs, feedback and attribution candidate.

- [ ] **Step 5: Run tests and commit**

```powershell
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval/test_validation.py -q
git add apps/api/src/tracerisk/challenge_eval/prompts.py apps/api/src/tracerisk/challenge_eval/validation.py apps/api/tests/challenge_eval/test_validation.py
git commit -m "feat: validate challenge model outputs"
```

### Task 4: DeepSeek client with complete attempt telemetry

**Files:**
- Create: `apps/api/src/tracerisk/challenge_eval/client.py`
- Create: `apps/api/tests/challenge_eval/test_client.py`

- [ ] **Step 1: Write failing transport tests**

Use `httpx.MockTransport` to test one successful non-stream HTTP attempt, timeout, 429, 500, permanent 401, malformed response, null content, non-stop finish reasons, usage details and absence of usage. Assert Authorization values and environment secrets never appear in serialized attempts. Multi-attempt budgeting belongs to the runner because a second attempt may be either a transport retry or a validation-feedback repair.

- [ ] **Step 2: Implement the client**

Send exactly the frozen model parameters, including disabled thinking, zero temperature, `max_tokens`, and JSON response format. Parse `model`, `system_fingerprint`, `finish_reason`, content, prompt cache hit/miss tokens, completion tokens and total tokens. Capture status, category and monotonic latency for both failed and successful attempts.

Expose one `complete_once(messages)` call that performs exactly one HTTP request. Classify timeout, transport, 429 and 5xx as retryable and all other HTTP failures as permanent, but never retry inside the client. The runner owns one shared budget of two HTTP attempts total. Do not sleep in tests; inject any backoff callback at the runner boundary.

- [ ] **Step 3: Run tests and commit**

```powershell
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval/test_client.py -q
git add apps/api/src/tracerisk/challenge_eval/client.py apps/api/tests/challenge_eval/test_client.py
git commit -m "feat: record DeepSeek challenge attempts"
```

### Task 5: Scoring, cost and attribution

**Files:**
- Create: `apps/api/src/tracerisk/challenge_eval/scoring.py`
- Create: `apps/api/src/tracerisk/challenge_eval/attribution.py`
- Create: `apps/api/tests/challenge_eval/test_scoring.py`
- Create: `apps/api/tests/challenge_eval/test_attribution.py`

- [ ] **Step 1: Write failing scoring tests**

Test first-attempt usable semantics, logical-call/attempt/retry counts, unique input/cited Chunk counts, null-preserving usage, conservative estimated cost, expected-versus-actual routing, false review and missed review counts, nullable failure-localization values, and field differences that do not pretend to be human edits.

Cost formula:

```python
cost = (
    cache_hit_tokens * cache_hit_rate
    + cache_miss_tokens * cache_miss_rate
    + output_tokens * output_rate
) / 1_000_000
```

- [ ] **Step 2: Write failing attribution tests**

Prove categories are mutually exclusive and ordered: structure, citation, support, human judgment. Freeze impact weights before results. Test ranking by weighted impact, then affected cases, measured human edits, diagnostic time and final deterministic category order. If either tied candidate contains any null diagnostic timing, skip the diagnostic-time tie-breaker for that tie and use the fixed category order; test this explicitly so crash recovery cannot bias selection. Test that an improvement manifest changing zero or more than one dimension is rejected.

- [ ] **Step 3: Implement scoring and attribution**

Use `Decimal` for rates/cost and serialize decimal strings. Record `gold_field_difference_count` separately from nullable `human_modified_field_count`. Attribute every non-ideal case once and include ideal cases as `None`, not `human_judgment`.

`failure_localization_ms` is supplied by the runner after two-phase persistence; scoring does not attempt to measure its own serialization. Keep it nullable only for a process crash during that finalization window, with an explicit `failure_localization_status="interrupted_before_finalize"` rather than an invented duration.

`select_improvement` must output the selected problem, evidence case IDs, aggregate impact and exactly one allowed dimension. The dimension mapping is deterministic but does not implement the improvement itself.

The saved analysis includes hashes of every baseline raw input and the frozen weights. Any consumer must recompute attribution from those hashed baseline artifacts and compare the complete normalized result with `analysis.json`; the file is evidence, never trusted authorization by itself.

- [ ] **Step 4: Run tests and commit**

```powershell
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval/test_scoring.py apps/api/tests/challenge_eval/test_attribution.py -q
git add apps/api/src/tracerisk/challenge_eval/scoring.py apps/api/src/tracerisk/challenge_eval/attribution.py apps/api/tests/challenge_eval/test_scoring.py apps/api/tests/challenge_eval/test_attribution.py
git commit -m "feat: score and attribute challenge cases"
```

### Task 6: Resumable runner, CLI and real review timing

**Files:**
- Create: `apps/api/src/tracerisk/challenge_eval/runner.py`
- Create: `scripts/run_model_challenge.py`
- Create: `apps/api/tests/challenge_eval/test_runner.py`

- [ ] **Step 1: Write failing orchestration tests**

Test fsynced append after every HTTP attempt, recovery after a crash between attempt one and attempt two, recovery after a successful response but before case scoring without another API call, two-phase diagnostic timing through the first durable case event, recovery during finalization, truncated-last-line recovery, completed-case resume without another API call, run fingerprint mismatch refusal, completed-run non-overwrite, full-source/AST improvement freeze comparison including the CLI wrapper, recomputed-analysis integrity, validation-feedback repair inside the shared two-attempt budget, and interactive review timing with injected input and monotonic clock.

- [ ] **Step 2: Implement staged commands**

Provide `check`, `baseline`, `analyze`, `improved`, `compare` and `review`. Require an explicit output path. A nonempty run directory is resumable only when its normalized manifest fingerprint is identical and its status is incomplete; refuse different fingerprints and refuse overwriting a completed run.

Require the key to exist as `MODEL_API_KEY` in the repository-root `.env`. Fail if an ambient `MODEL_API_KEY` would override it, so key provenance is unambiguous. Never print its value, prefix, hash or length, and never serialize it.

The runner should accept injectable client/clock/input functions for tests. Before any model call, write a normalized manifest containing dataset/config/lock hashes; Python, OS and exact installed dependency versions; model parameters; pricing/retry policy; impact weights; the CLI entrypoint hash; a complete file-hash inventory for the evaluation and imported production-validation source surface; and normalized AST-node inventories that allow the verifier to isolate exactly one selected function patch.

Write every `AttemptRecord` immediately to append-only `attempts.jsonl` using UTF-8 compact sorted JSON, then flush and `os.fsync`. The journal includes the safe response content needed to finish parsing after a crash. On resume, replay it: a recorded successful response is scored without another call; a recorded retryable failure may consume the remaining shared attempt; two recorded attempts can never trigger a third.

Measure failure localization with two-phase persistence. After the final HTTP attempt, start the monotonic interval, parse/validate/attribute/serialize the case payload, append that payload without the duration to fsynced `case-events.jsonl`, then stop the clock. Append the finalized `CaseResult`, including the measured duration, to fsynced `cases.jsonl`. Thus the measured interval includes durable result-event persistence without depending on a value not yet known. If the process crashes after the event fsync but before finalization, resume creates the final case with a null duration and `interrupted_before_finalize`; it never guesses elapsed time or repeats the model call.

The two-attempt runner loop handles transport failures and model repair. A retryable transport failure resends the original messages. A parse or validation failure appends the current validation-feedback message and uses the second and final attempt. The feedback builder is active in the baseline, making `validation_feedback` a real selectable dimension rather than dead configuration.

- [ ] **Step 3: Implement review semantics**

Only show cases whose actual action is manual review. Start timing after display and stop after a valid decision. The submitted draft may change only `claim_code`, `title`, `summary`, `reasoning`, `evidence_citations`, `evidence_sufficiency`, `confidence_level`, and `review_recommended`; `finding_key` and `risk_type` are immutable. Validate a modified draft through the same production support boundary before accepting it.

Append every decision to a separate fsynced `reviews.jsonl`; never mutate `attempts.jsonl` or `cases.jsonl`. Persist only case ID, decision, allowed changed-field names/count, duration and validation status. Do not persist reviewer-entered rationale or replacement field values, so free text and possible sensitive content cannot enter committed artifacts. Add tests with a sentinel secret string and assert it is absent from the journal and reports. Regenerate summaries by joining raw case results with review records. Until review runs, keep both human metrics null and status `not_measured`.

- [ ] **Step 4: Run tests and CLI help**

```powershell
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval/test_runner.py -q
apps/api/.venv/Scripts/python.exe scripts/run_model_challenge.py --help
```

Expected: tests pass and help lists six subcommands without reading the API key.

- [ ] **Step 5: Commit**

```powershell
git add apps/api/src/tracerisk/challenge_eval/runner.py scripts/run_model_challenge.py apps/api/tests/challenge_eval/test_runner.py
git commit -m "feat: add resumable challenge evaluation CLI"
```

### Task 7: Reporting from raw results only

**Files:**
- Create: `apps/api/src/tracerisk/challenge_eval/reporting.py`
- Create: `apps/api/tests/challenge_eval/test_reporting.py`

- [ ] **Step 1: Write failing report tests**

Use tiny attempt, case-event, finalized-case and review JSONL fixtures and assert all summary numbers are derived, nullable measurements render as `not measured`, typical cases include success/failure/manual review when present, and the resume bullet contains problem, one change, process-metric delta and limitations. Add a sentinel number not present in raw inputs and assert it never appears.

- [ ] **Step 2: Implement aggregation and Markdown rendering**

Render counts and distributions rather than a headline pass rate. Read and validate the manifest plus `attempts.jsonl`, `case-events.jsonl`, `cases.jsonl`, and optional `reviews.jsonl`; store every input hash in the summary/report provenance block. Attempt and cost metrics come from the attempt journal, diagnostic metrics from finalized cases, and human metrics only from the review journal. Include model/fingerprint, hashes, environment, contamination warning, measured/null human metrics, API failures and limitations. Select typical cases deterministically by case ID after severity.

If the improved run does not improve the selected process metric, generate a truthful experimental conclusion instead of achievement wording.

- [ ] **Step 3: Run tests and commit**

```powershell
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval/test_reporting.py -q
git add apps/api/src/tracerisk/challenge_eval/reporting.py apps/api/tests/challenge_eval/test_reporting.py
git commit -m "feat: report challenge evaluation evidence"
```

### Task 8: Run the unoptimized baseline and make exactly one improvement

**Files:**
- Create: `docs/evaluation/model-challenge/baseline/manifest.json`
- Create: `docs/evaluation/model-challenge/baseline/attempts.jsonl`
- Create: `docs/evaluation/model-challenge/baseline/case-events.jsonl`
- Create: `docs/evaluation/model-challenge/baseline/cases.jsonl`
- Create: `docs/evaluation/model-challenge/baseline/reviews.jsonl` only if a human review is performed
- Create: `docs/evaluation/model-challenge/baseline/summary.json`
- Create: `docs/evaluation/model-challenge/connectivity-check.json`
- Create: `docs/evaluation/model-challenge/analysis.json`
- Modify exactly one authorized implementation/config dimension based on `analysis.json`
- Add one focused regression test beside the selected dimension

- [ ] **Step 1: Verify the secret and endpoint without printing the key**

Run one explicit connectivity request through the evaluation client. The command reports only success/failure, base URL, requested/returned model, system fingerprint, usage, latency and estimated cost. It never reports key metadata. Its attempt is stored in `connectivity-check.json` and excluded from the 40-case metrics.

```powershell
apps/api/.venv/Scripts/python.exe scripts/run_model_challenge.py check `
  --config datasets/evaluation/model_challenge_config.json `
  --output docs/evaluation/model-challenge/connectivity-check.json
```

- [ ] **Step 2: Run the real baseline once**

```powershell
$env:PYTHONPATH = 'apps/api/src;.'
apps/api/.venv/Scripts/python.exe scripts/run_model_challenge.py baseline `
  --dataset datasets/evaluation/model_challenge_cases.jsonl `
  --config datasets/evaluation/model_challenge_config.json `
  --output docs/evaluation/model-challenge/baseline
```

Do not inspect or edit the dataset after the baseline. If interrupted, rerun the identical command and rely on fingerprinted resume.

- [ ] **Step 3: Either review baseline now or keep both runs unreviewed**

If a real human operator will provide review measurements, run baseline review before attribution so measured edits are available to the documented tie-breaker:

```powershell
apps/api/.venv/Scripts/python.exe scripts/run_model_challenge.py review `
  --run docs/evaluation/model-challenge/baseline
```

Otherwise do not create baseline reviews and leave the human metrics null. The improved run must follow the same choice; comparison rejects one-sided review measurement.

- [ ] **Step 4: Generate attribution**

```powershell
apps/api/.venv/Scripts/python.exe scripts/run_model_challenge.py analyze `
  --baseline docs/evaluation/model-challenge/baseline `
  --output docs/evaluation/model-challenge/analysis.json
```

Inspect the selected problem, case IDs and authorized dimension. When human metrics are unmeasured, skip that tie-breaker and proceed to failure-localization time; if either tied candidate has incomplete diagnostic timing, skip that tie-breaker too and use fixed category order. Never substitute gold differences. Before accepting the analysis, recompute attribution from the hashed baseline manifest/journals and frozen weights and require byte-equivalent normalized output. Editing `analysis.json` cannot authorize a different dimension. Record the decision in the commit message/body.

- [ ] **Step 5: Write one failing regression test for the selected problem**

Choose only one branch:

- Prompt: add a failing prompt-content/behavior fixture test, then modify only `challenge_eval/prompts.py`.
- Validation feedback: add a failing feedback classification test, then modify only the feedback path in `challenge_eval/validation.py`.
- Context ordering: add a failing ordering test, then modify only the context-order function; dataset bytes stay unchanged.
- Review routing: add a failing routing test, then modify only routing logic in `challenge_eval/validation.py`.

Do not change the dataset, model parameters, retry policy, pricing, scoring, impact weights or any second behavior dimension.

- [ ] **Step 6: Implement the minimum selected change and pass its test**

Run the single regression test, the full `apps/api/tests/challenge_eval` suite, and the normalized manifest diff guard. The guard compares the complete source-file inventory and AST-node inventory plus every frozen data/config/runtime digest. It must print exactly one selected function node as `changed_dimension`; any shared helper, import, constant, decorator, production dependency or second AST-node change fails.

- [ ] **Step 7: Commit baseline evidence and the single change**

```powershell
git add docs/evaluation/model-challenge/baseline docs/evaluation/model-challenge/analysis.json apps/api/src/tracerisk/challenge_eval apps/api/tests/challenge_eval
git commit -m "eval: address measured <selected-problem>"
```

Replace `<selected-problem>` with the actual attribution; do not preselect it.

### Task 9: Rerun, compare, document and validate

**Files:**
- Create: `docs/evaluation/model-challenge/improved/manifest.json`
- Create: `docs/evaluation/model-challenge/improved/attempts.jsonl`
- Create: `docs/evaluation/model-challenge/improved/case-events.jsonl`
- Create: `docs/evaluation/model-challenge/improved/cases.jsonl`
- Create: `docs/evaluation/model-challenge/improved/reviews.jsonl` only if baseline review was measured
- Create: `docs/evaluation/model-challenge/improved/summary.json`
- Create: `docs/evaluation/model-challenge/comparison.md`
- Create: `docs/evaluation/model-challenge/typical-cases.md`
- Create: `docs/evaluation/model-challenge/resume-bullet.md`
- Create: `docs/evaluation/model-challenge/README.md`
- Modify: `README.md`
- Modify: `docs/resume-bullets.md`

- [ ] **Step 1: Run the improved evaluation on the frozen dataset**

```powershell
apps/api/.venv/Scripts/python.exe scripts/run_model_challenge.py improved `
  --dataset datasets/evaluation/model_challenge_cases.jsonl `
  --config datasets/evaluation/model_challenge_config.json `
  --baseline docs/evaluation/model-challenge/baseline `
  --analysis docs/evaluation/model-challenge/analysis.json `
  --output docs/evaluation/model-challenge/improved
```

The command must first recompute attribution from the hashed baseline artifacts and frozen weights and require an exact normalized match with `analysis.json`. It must then refuse to run if the CLI wrapper, Python/OS/dependencies, dataset, model parameters, pricing, retry policy, scoring, impact weights or any unauthorized behavior-component digest differs. Only the recomputed selected component digest may change. A provider `system_fingerprint` change is not client-controlled: allow completion but mark the comparison contaminated.

- [ ] **Step 2: Match baseline human-review provenance**

If and only if baseline review was performed, run improved review before comparison:

```powershell
apps/api/.venv/Scripts/python.exe scripts/run_model_challenge.py review `
  --run docs/evaluation/model-challenge/improved
```

If baseline review was not performed, do not review the improved run; leave both sides' review time and modified fields null. Do not auto-fill from gold labels. `compare` rejects asymmetric review provenance.

- [ ] **Step 3: Generate comparison artifacts**

```powershell
apps/api/.venv/Scripts/python.exe scripts/run_model_challenge.py compare `
  --baseline docs/evaluation/model-challenge/baseline `
  --improved docs/evaluation/model-challenge/improved `
  --analysis docs/evaluation/model-challenge/analysis.json `
  --output docs/evaluation/model-challenge
```

Recompute analysis from baseline raw inputs before comparison and reject a mismatching `analysis.json`. Verify every report number can be reproduced from the exact manifest, attempt journal, case-event journal, finalized-case journal and optional review journal whose hashes appear in report provenance. If model or `system_fingerprint` changed, retain the comparison but label causal attribution contaminated.

- [ ] **Step 4: Update README and resume evidence**

Link the commands and reports. Insert the generated bullet verbatim or with punctuation-only editing. Preserve limitations: synthetic, 40 cases, single run pair, actual DeepSeek identifier/fingerprint and local non-production environment.

- [ ] **Step 5: Run focused and existing regression gates**

```powershell
$env:MODEL_ADAPTER = 'mock'
$env:EMBEDDING_PROVIDER = 'fake'
$env:EMBEDDING_MODEL = 'fake-sha256-v1'
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/challenge_eval -q
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/evaluation/test_citations.py apps/api/tests/unit/ai/test_citation_support.py -q
apps/api/.venv/Scripts/python.exe -m pytest apps/api/tests/evaluation/test_rules.py apps/api/tests/unit/rules -q
apps/api/.venv/Scripts/python.exe -m ruff check apps/api/src/tracerisk/challenge_eval apps/api/tests/challenge_eval scripts/run_model_challenge.py
apps/api/.venv/Scripts/python.exe -m ruff format --check apps/api/src/tracerisk/challenge_eval apps/api/tests/challenge_eval scripts/run_model_challenge.py
apps/api/.venv/Scripts/python.exe -m mypy apps/api/src/tracerisk/challenge_eval
git diff --check
```

Expected: all new tests pass; the existing citation and rule gates remain green and are reported as regression gates, not model-quality achievements.

- [ ] **Step 6: Commit final measured artifacts**

```powershell
git add docs/evaluation/model-challenge README.md docs/resume-bullets.md
git commit -m "docs: publish measured model challenge evaluation"
```

## Final audit

- [ ] Confirm `git status --short` is clean or contains only pre-existing unrelated user changes.
- [ ] Search committed artifacts for the API key and Authorization header; expect zero matches.
- [ ] Confirm every HTTP call has one fsynced attempt-journal record and no case exceeds two attempts.
- [ ] Recompute dataset, prompt, config and lock hashes and compare them with both manifests.
- [ ] Recompute `analysis.json` from baseline journals/weights and verify its selected dimension exactly.
- [ ] Confirm the CLI and complete transitive local source inventory are frozen; the only AST difference is the selected function.
- [ ] Confirm baseline and improved raw JSONL each contain 40 unique case IDs.
- [ ] Confirm exactly one behavior dimension changed and no dataset gold label changed.
- [ ] Confirm null human metrics are rendered as not measured, not zero.
- [ ] Confirm review measurements are present for both runs or neither and live only in append-only review journals.
- [ ] Confirm the resume bullet quotes only values present in generated summaries.
- [ ] Report actual changes, commands run, observed failures/risks, external API cost method and experiment limitations.
