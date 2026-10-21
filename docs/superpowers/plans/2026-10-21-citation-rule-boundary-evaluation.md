# Citation and Rule Boundary Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reproducible 35-case citation-boundary evaluation and 24-case deterministic-rule evaluation to the existing Stage 6 Mock/Fake runner, with auditable per-case outcomes, requested metrics, and defect-regression closure.

**Architecture:** Keep labeled case definitions in a focused evaluation catalog and execute them through existing production boundaries: `validate_findings` plus `requires_review` for citation disposition, and conflict resolution plus `evaluate_all_rules_from_facts` for scoring. Extend the existing evaluation publisher instead of adding a second CLI, and preserve isolated-database ownership and failure publication behavior.

**Tech Stack:** Python 3.12, Pydantic 2, SQLAlchemy async, PostgreSQL, pytest, Ruff, Mypy, existing Stage 6 evaluation CLI.

---

## File Structure

- Create `apps/api/src/tracerisk/evaluation/boundary_cases.py`: immutable citation and rule case labels, expected dispositions, expected scores, reasons, and boundary tags.
- Modify `apps/api/src/tracerisk/evaluation/runner.py`: materialize dynamic citation mutations from frozen retrieval, classify outcomes, calculate six requested metrics, and execute the 24 rule cases.
- Modify `apps/api/src/tracerisk/evaluation/metrics.py`: add a small count-result type only if needed for boundary failure counts; keep ratio metrics unchanged.
- Modify `apps/api/tests/evaluation/test_citations.py`: verify the 35-case catalog, one-field mutation invariant, outcome classification, metric denominators, and program-error exclusion.
- Modify `apps/api/tests/evaluation/test_rules.py`: verify 24 cases, six per rule, declared expectations, independent judgment/score metrics, and boundary failure counts.
- Modify `apps/api/tests/evaluation/test_cli.py`: verify JSON/Markdown publication of boundary cases and metrics and update old 13-case assertions.
- Modify `scripts/run_evaluation.py`: publish new metric groups, per-case tables, program errors, limitations, and source hashes.
- Modify `docs/evaluation/results.json` and `docs/evaluation/results.md`: regenerate final evidence after all checks pass.
- Conditionally create `docs/evaluation/boundary-known-failures.md`: preserve unresolved case evidence only when a product mismatch cannot be safely fixed within the confirmed rules.
- Conditionally modify the smallest affected file under `apps/api/src/tracerisk/ai/` or `apps/api/src/tracerisk/rules/`, plus its focused test, only if the first run exposes a real defect.

### Task 1: Add the fixed boundary-case catalog

**Files:**
- Create: `apps/api/src/tracerisk/evaluation/boundary_cases.py`
- Modify: `apps/api/tests/evaluation/test_citations.py`
- Modify: `apps/api/tests/evaluation/test_rules.py`

- [ ] **Step 1: Write failing catalog-shape tests**

Add tests asserting:

```python
assert len(CITATION_CASES) == 35
assert Counter(case.category for case in CITATION_CASES) == {
    "valid": 10,
    "other_company_evidence": 5,
    "other_run_chunk": 5,
    "changed_digit": 5,
    "noncontiguous_excerpt": 5,
    "unsupported_conclusion": 5,
}
assert len(RULE_CASES) == 24
assert {case.rule_code for case in RULE_CASES} == {
    "JUDICIAL_OPEN_CASE_V1",
    "BUSINESS_ABNORMAL_V1",
    "NEGATIVE_NEWS_V1",
    "CORE_IP_ANOMALY_V1",
}
assert set(Counter(case.rule_code for case in RULE_CASES).values()) == {6}
```

Also require unique case IDs, a non-empty expected reason, exactly one rule code per rule case, and boundary tags drawn from `date`, `deduplication`, `status`, and `ownership`.
For every five-case citation mutation category, assert that the descriptors vary
at least one of baseline finding, citation position, or mutation position rather
than mechanically repeating one object.

- [ ] **Step 2: Run the catalog tests and confirm they fail**

Run:

```powershell
Set-Location apps/api
uv run pytest tests/evaluation/test_citations.py tests/evaluation/test_rules.py -q
```

Expected: collection or import failure because `boundary_cases.py` and the new catalogs do not exist.

- [ ] **Step 3: Implement immutable case definitions**

Create frozen dataclasses and literal types equivalent to:

```python
CitationDisposition = Literal["accepted", "rejected", "manual_review"]

@dataclass(frozen=True)
class CitationCase:
    case_id: str
    category: str
    baseline_index: int
    expected_disposition: CitationDisposition
    expected_reason: str

@dataclass(frozen=True)
class RuleCase:
    case_id: str
    rule_code: str
    domain: str
    as_of_date: date
    facts: tuple[FactLabel, ...]
    expected_codes: tuple[str, ...]
    expected_score: int
    expected_total: int
    expected_keys: tuple[str, ...]
    expected_reason: str
    boundary_tags: tuple[str, ...]
    expected_conflict: bool = False
    expected_review: bool = False
```

Define 10 legal citation descriptors and five descriptors for each requested mutation category. For unsupported conclusions, label structured claim/domain contradictions as `rejected` and any deliberately unverified free-text extension as `manual_review`; the expected reason must make the distinction explicit.

Define six cases for each frozen rule code. Include positive, negative, and boundary cases and cover same-key duplication, D−364/D−365, future observations, resolved state timing, non-core ownership, and multiple matching records without duplicate points.

- [ ] **Step 4: Run catalog tests**

Run the command from Step 2.

Expected: catalog-shape tests pass; execution tests may still fail until later tasks.

- [ ] **Step 5: Commit the catalog**

```powershell
git add apps/api/src/tracerisk/evaluation/boundary_cases.py apps/api/tests/evaluation/test_citations.py apps/api/tests/evaluation/test_rules.py
git commit -m "test: define citation and rule boundary cases"
```

### Task 2: Execute and measure the 24 rule cases

**Files:**
- Modify: `apps/api/src/tracerisk/evaluation/runner.py`
- Modify: `apps/api/tests/evaluation/test_rules.py`

- [ ] **Step 1: Write failing rule-result tests**

Require `evaluate_rule_cases` to return 24 detailed rows and these aggregates:

```python
assert result["judgment_accuracy"].denominator == 24
assert result["score_exact_match"].denominator == 24
assert result["boundary_failures"] == {
    "date": 0,
    "deduplication": 0,
    "status": 0,
    "ownership": 0,
}
```

Each row must include the declared `as_of_date`, expected and actual rule codes,
match, score, total, keys, conflict/review result, reason, boundary tags,
`failed_fields`, and separate booleans for judgment and score correctness. Add a
regression assertion that three repeated instances of one judicial case still
produce 35 points and one matched key.

- [ ] **Step 2: Run the focused rule tests and confirm failure**

```powershell
Set-Location apps/api
uv run pytest tests/evaluation/test_rules.py -q
```

Expected: failure because the runner still exposes the 13-case combined exact-match result.

- [ ] **Step 3: Move case ownership to the catalog and update evaluation**

Import `RULE_CASES` and `FactLabel` from `boundary_cases.py`. For every case:

1. Build deterministic `ConflictFact` rows.
2. Call `evaluate_conflicts(..., as_of_date=case.as_of_date)`.
3. Call `evaluate_all_rules_from_facts` with resolved facts and
   `as_of_date=case.as_of_date`.
4. Select the case's declared rule evaluation while retaining the complete tuple
   of actual matched rule codes.
5. Compare actual codes, match, fixed score, matched keys, declared expected
   total, conflict, and review expectation independently against non-derived
   labels from the catalog.
6. Populate `failed_fields` with every mismatched output field and increment each
   tagged boundary failure count once when the case fails.

Create `ratio_metric` results named `rule_judgment_accuracy` and `rule_score_exact_match`. Retain per-domain metrics only if they are calculated from the same 24 fixed cases and do not replace the requested aggregates.

- [ ] **Step 4: Run the focused rule tests**

Run the command from Step 2.

Expected: all rule evaluation tests pass with denominators of 24 and six cases per rule.

- [ ] **Step 5: Commit rule execution**

```powershell
git add apps/api/src/tracerisk/evaluation/runner.py apps/api/tests/evaluation/test_rules.py
git commit -m "feat: evaluate 24 deterministic rule boundaries"
```

### Task 3: Execute and classify the 35 citation cases

**Files:**
- Modify: `apps/api/src/tracerisk/evaluation/runner.py`
- Modify: `apps/api/tests/evaluation/test_citations.py`

- [ ] **Step 1: Write failing citation-runner tests**

Add database-backed tests requiring:

- exactly 35 result rows and the exact category counts from Task 1;
- 10 legal cases classified as `accepted`;
- recognized `CitationValidationError` failures classified as `rejected`;
- validated findings with `support_status == "indeterminate"` and `requires_review(...)` classified as `manual_review`;
- all other exceptions classified as `program_error` with exception type and phase;
- every mutation changes only the declared field or conclusion component;
- changed-digit and noncontiguous excerpts are absent from the stored chunk, so
  their rejection cannot pass accidentally or test a different condition;
- no case writes an `AIFinding` or changes rule scores.

Require every detailed result row to contain this auditable schema:

```text
case_id, category, baseline_id, mutation_field, expected_disposition,
expected_reason, actual_disposition, actual_reason, conforms_to_expectation,
program_error_type, program_error_phase
```

`actual_reason` must be a normalized validator/support/routing reason rather than
an exception traceback. Legal cases have a null mutation field; mutation cases
must name the one declared semantic mutation.

Add a pure aggregation test where one invalid case raises `RuntimeError`. Assert it increases `program_error_count`, does not increase successful rejection, and makes expected-treatment conformity false.

- [ ] **Step 2: Run focused citation tests and confirm failure**

```powershell
Set-Location apps/api
uv run pytest tests/evaluation/test_citations.py -q
```

Expected: failure because only the existing persisted-citation metric is available.

- [ ] **Step 3: Materialize two frozen retrieval boundaries**

In the isolated evaluation database, create and execute at least two investigations for different synthetic companies. Build a pool of valid `RiskFindingDraft` baselines from persisted Mock findings and frozen chunks. Before mutation, run all 10 legal descriptors through `validate_findings` and fail setup if any baseline is not accepted.

For mutation materialization:

- `other_company_evidence`: replace only `evidence_id` with a real Evidence ID from the other company.
- `other_run_chunk`: replace the full citation identity needed to make the chunk internally real, but outside the evaluated Run; record this as the single semantic mutation `citation_target`.
- `changed_digit`: choose an excerpt containing a digit and replace exactly one digit.
- `noncontiguous_excerpt`: concatenate two non-adjacent substrings from the same stored chunk without changing identity fields.
- `unsupported_conclusion`: keep the citation byte-for-byte intact and change only the claim/domain or free-text conclusion declared by the case.

If existing synthetic text cannot supply five genuine numeric excerpts, add evaluation-only frozen chunks in the isolated database rather than modifying product catalog data.

- [ ] **Step 4: Classify real outcomes and calculate metrics**

For each case, call `validate_findings`. Catch only `CitationValidationError` as a business rejection. For a validated result, use its support fields with `requires_review` to distinguish trusted acceptance from manual review. Catch unexpected exceptions at the per-case boundary and store `program_error`; continue running remaining cases.

Produce:

```text
invalid_citation_acceptance_rate
valid_citation_false_block_rate
expected_treatment_conformity
```

The first metric's denominator is the 25 non-valid cases and its numerator counts
only `accepted`. The second denominator is the 10 valid cases and numerator
counts only `rejected`; program errors remain separate and are not reclassified
as rejections. The third denominator is all 35 cases and numerator counts exact
disposition matches; `program_error` never matches.

Store category-level leak counts, `program_error_count`, and detailed rows separately from ratios.

- [ ] **Step 5: Run citation tests**

Run the command from Step 2.

Expected: 35 cases execute, program errors are zero, and every metric can be recomputed from detailed rows.

- [ ] **Step 6: Commit citation execution**

```powershell
git add apps/api/src/tracerisk/evaluation/runner.py apps/api/tests/evaluation/test_citations.py
git commit -m "feat: evaluate citation validation boundaries"
```

### Task 4: Publish the new evidence without weakening failure handling

**Files:**
- Modify: `scripts/run_evaluation.py`
- Modify: `apps/api/tests/evaluation/test_cli.py`

- [ ] **Step 1: Write failing publisher tests**

Extend CLI tests to assert that incremental and final documents agree and contain:

```text
mock.citation_boundary_cases (35 rows)
mock.rule_cases (24 rows)
mock.citation_program_errors
mock.rule_boundary_failures
mock.metrics.invalid_citation_acceptance_rate
mock.metrics.valid_citation_false_block_rate
mock.metrics.expected_treatment_conformity
mock.metrics.rule_judgment_accuracy
mock.metrics.rule_score_exact_match
```

Assert Markdown includes the six metrics, case counts, program-error count, failure categories, and the limitation that this does not measure all hallucinations. Update the stale 13-case assertion to 24.

- [ ] **Step 2: Run CLI tests and confirm failure**

```powershell
Set-Location apps/api
uv run pytest tests/evaluation/test_cli.py -q
```

Expected: failure because `_metric_document`, `_record_metric_group`, and `_render_markdown` do not know the boundary result shapes.

- [ ] **Step 3: Extend source hashes and schema version**

Add SHA-256 entries for the approved 2026-10-21 spec, implementation plan, and boundary case catalog. Change the result schema version to a new explicit value so older consumers cannot mistake the enriched document for `stage6-evaluation-v1`.

- [ ] **Step 4: Publish boundary groups and Markdown summaries**

Teach `_record_metric_group` and `_metric_document` to serialize the three citation and two rule ratio metrics plus detailed rows and count summaries. Extend `_render_markdown` with compact citation and rule sections. Do not expose traceback text, sensitive settings, or exception messages beyond normalized exception type and evaluation phase.

Preserve marker behavior, atomic replacement, current-failure publication, retained-database comparison, and cleanup semantics.

- [ ] **Step 5: Run CLI tests**

Run the command from Step 2.

Expected: all CLI publication and failure-path tests pass.

- [ ] **Step 6: Commit publisher changes**

```powershell
git add scripts/run_evaluation.py apps/api/tests/evaluation/test_cli.py
git commit -m "feat: publish boundary evaluation evidence"
```

### Task 5: Close any defects exposed by the first complete run

**Files:**
- Modify only if needed: `apps/api/src/tracerisk/ai/validation.py`
- Modify only if needed: one file under `apps/api/src/tracerisk/rules/`
- Modify only if needed: the corresponding focused test file

- [ ] **Step 1: Run all 59 cases before changing production behavior**

```powershell
Set-Location apps/api
uv run pytest tests/evaluation/test_citations.py tests/evaluation/test_rules.py -q
```

Expected: obtain the first complete result. Save the exact failing case IDs and actual reasons in the implementation notes before any fix.

- [ ] **Step 2: Decide whether each failure is product behavior, label error, or harness error**

Compare the case against `docs/design/evidence-contract.md` and `docs/design/rule-specification.md`. Correct a mislabeled test or harness bug without claiming a product fix. Treat a validator bypass, wrong routing outcome, duplicate score, date error, state error, or ownership error as a product defect.

- [ ] **Step 3: Add a focused regression assertion for each product defect**

Run the narrowest failing test and verify that it fails for the documented reason before editing production code.

- [ ] **Step 4: Implement the smallest production fix**

Change only the affected validation or rule function. Do not add new claim types, rule codes, scores, dependencies, or configuration.

- [ ] **Step 5: Re-run focused and full boundary tests**

Run the focused regression followed by the Step 1 command.

Expected: all 59 cases complete with zero program errors. If a product mismatch
cannot be safely fixed without changing confirmed business rules or expanding
scope, stop the success path: preserve the failing regression and case output,
mark the evaluation failed, and follow Step 7 instead of proceeding as though
the gate passed.

- [ ] **Step 6: Commit only if a product defect was fixed**

For a citation defect, stage `apps/api/src/tracerisk/ai/validation.py` and the
focused citation test. For a rule defect, stage exactly one of
`business.py`, `judicial.py`, `news.py`, or `intellectual_property.py` and its
matching unit test under `apps/api/tests/unit/rules/`. Then commit with:

```powershell
git commit -m "fix: enforce evaluated boundary condition"
```

- [ ] **Step 7: Handle an unresolved product mismatch only when necessary**

Create `docs/evaluation/boundary-known-failures.md` containing the failing case
IDs, expected and actual results, normalized reason, attempted diagnosis, and the
specific decision or authority needed for a fix. Keep the focused regression
failing, run the guarded evaluation so `results.json` records `status=failed`,
and preserve the known-failures document while avoiding any commit that presents
the result as success. Report this blocked validation state to the user; Tasks 6
Steps 2–6 are conditional on resolving the mismatch.

### Task 6: Run quality gates and regenerate final artifacts

**Files:**
- Modify: `docs/evaluation/results.json`
- Modify: `docs/evaluation/results.md`

- [ ] **Step 1: Run formatting and static checks**

```powershell
Set-Location apps/api
uv run ruff check src tests ../../scripts
uv run mypy src ../../scripts/run_evaluation.py
```

Expected: both commands exit 0. Fix only issues introduced by this work.

- [ ] **Step 2: Run focused evaluation tests**

```powershell
Set-Location apps/api
uv run pytest tests/evaluation/test_citations.py tests/evaluation/test_rules.py tests/evaluation/test_cli.py -q
```

Expected: all focused tests pass. If Task 5 Step 7 was required, stop here after
capturing the failing output; do not execute or claim the remaining success
steps.

- [ ] **Step 3: Run the guarded Stage 6 evaluation**

```powershell
Set-Location ../..
python scripts/run_evaluation.py
```

Expected: exit 0, isolated database dropped, marker removed, 35 citation cases
and 24 rule cases in `results.json`, and matching JSON hash in `results.md`.
This step is not run as a success gate while a known focused regression remains
unresolved.

- [ ] **Step 4: Verify artifact invariants**

Use a read-only script or pytest assertion to recompute all five ratios and boundary failure counts from the detailed rows. Confirm no program error was counted as a rejection, result counts are 35/24, and Markdown states the scope limitation.

- [ ] **Step 5: Record actual findings honestly**

If defects were found, report failing case IDs, defect classes, and before/after absolute ratios. If none were found, report only the validated scope and absolute results. Do not claim general hallucination prevention.

- [ ] **Step 6: Commit final evidence**

```powershell
git add docs/evaluation/results.json docs/evaluation/results.md
git commit -m "docs: record boundary evaluation results"
```
