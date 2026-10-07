# Stage 6 Task 3 E2E acceptance checklist

> Historical Task 3 snapshot. Current Git, hosted CI, Docker, and rehearsal status is maintained only in the [acceptance entry](../acceptance.md).

All eight paths passed in each of the two fresh owned-database browser passes selected below.
Each normal phase reported **7 passed / 1 skipped** (only path 7); the sequential
invalid-citation phase reported **1 passed** (path 7). No intercepted fixtures count
as acceptance. The public human replacement finding contract and migration `0012`
close the former Path 3 product gap. Original AI content is retained separately.

The earlier selected `965e2cb1d49640678684b979ec947952` /
`a7a2aa7bfb524b6db65047df9c614457` results and post-R7 regression
`71d27ed3d47c4cfeace537be77f38881` were 7/8 historical results. They are
superseded by the two 8/8 passes below and remain listed as history in
`e2e-results.json`. Failed current attempts `fdfc76e574ed4e61aaafcffc53f55734`
and `689f3a054ebf4672bd52e9797b81cfb3` were cleaned and are not counted as passes.

| Path | Scenario and preconditions | UI action and visible assertion | Public API and read-only database assertion | Actual result and safe evidence |
|---|---|---|---|---|
| 1. Low risk | `clean-operations`, analyst, normal worker | Login, open investigation and report; timeline shows completion and report page loads | `completed`, score 0, no decision, exactly one report and `run.completed` | Passed in both clean passes; IDs and state references below. |
| 2. High risk | `all-rules-95`, distinct analyst/reviewer, normal worker | Reviewer sees pending finding; analyst sees review wait, resumed completion and report link | Score 95, finding decisions, one resume Outbox/event and one report | Passed in both clean passes; IDs and state references below. |
| 3. Modified finding | `all-rules-95`, distinct analyst/reviewer, public modify contract | Reviewer selects visible modify label, verifies prefill/required fields, edits title/summary/reasoning and submits; original AI, decision and human version appear separately; report uses edited final text | Original AI content hash unchanged; exactly one human version/modify decision; final source `human` links the original AI; score unchanged and one report/resume/completion event | Passed in both selected clean passes; `runs[2]` below. |
| 4. Rejected finding | `all-rules-95`, distinct analyst/reviewer, normal worker | Analyst sees original AI and rejection, then final report | Original AI and rejection persist; final findings exclude rejected AI; one report | Passed in both clean passes; IDs and state references below. |
| 5. Authority conflict | `cross-source-conflict`, normal worker | Investigation displays the `CONFLICT-CASE-001` conflict record with judicial/open and business/closed sides, judicial authority/open resolution, and review-wait timeline | Resolved authoritative conflict still forces review with score below 60; no report before review | Passed in both clean passes; IDs and state references below. |
| 6. Missing coverage | `unavailable-source`, normal worker | Investigation displays insufficient coverage and run-level review | Zero score, no AI finding, `awaiting_review`, no report | Passed in both clean passes; IDs and state references below. |
| 7. Invalid citation | `all-rules-95`; normal worker stopped, E2E-only replacement selected with one named mutation | Investigation timeline shows failed Run and no final report | Durable `run.failed`, no AI finding/report, completed rule node but no completed AI node; focused citation tests cover ID, chunk, hash, excerpt and outside-frozen-set rejection | Passed in both clean passes; IDs and state references below. |
| 8. Duplicate replay | `all-rules-95`, normal worker, distinct analyst/reviewer | Investigation still shows one completed Run/report after repeat review and replay | Same-key review returns same decision; guarded resend requires the original job to have succeeded, then the exact new replay job must succeed before one decision/resume transition/report/terminal event and stable report bytes are asserted | Passed in both clean passes; IDs and state references below. |

## Runtime evidence

P1 = `886b91bc8b194f6e8499119369b379b0`; P2 = `046afad18d1b4fc889578e1771a2bfde`.
Each used local Vite Web + public API + sequential guarded combined workers +
PostgreSQL on a fresh recorded `tracerisk_e2e_<32 lowercase hex>` database at
Alembic `0012`. Guarded cleanup completed for both databases. Success-only
screenshots were not retained; traces/videos remained off. State JSON is a
whitelisted read-only database capture; UI assertions are in the browser suite.

| Path | P1 Run / report | P2 Run / report | Safe state evidence |
|---|---|---|---|
| 1 | e308039f-c38c-41e3-b818-e591b0e2573d / e02d444a-3d57-41d2-b462-5c366c89d942 | c17056a0-0e3b-4907-8c2e-f4c1059fd89f / 8074fdf1-ae4e-4070-a0d4-4373c4228d37 | [P1](e2e-results.json) `runs[0]`; [P2](e2e-results.json) `runs[0]` |
| 2 | 28ac6a25-9822-44a3-95a2-a175b001980f / 4fdf39dd-704b-4b27-84d4-9921fec010ff | f5454b87-676d-4365-8853-ac48f3e3fbe9 / e2ad0416-c39c-4d9b-87cf-6d48ce88d69f | [P1](e2e-results.json) `runs[1]`; [P2](e2e-results.json) `runs[1]` |
| 3 | 09fa0899-b83f-4fe9-9909-be5174bed30b / bcb4ed52-2be1-4207-9849-a8584260ba32 | fa5a3062-cac6-44e6-bcd7-50a09f4650aa / 4484c267-cb15-4a00-9ddf-86820552f08e | [P1](e2e-results.json) `runs[2]`; [P2](e2e-results.json) `runs[2]` |
| 4 | 016f7d0f-2428-41c0-96ad-f6bd8017aa14 / 1ce8734b-d750-48a8-942e-70a9e667b336 | 2d51b1cb-d540-445a-8de4-147faa46ec0e / d7c6034f-fa06-4da0-bebb-233df90d645c | [P1](e2e-results.json) `runs[3]`; [P2](e2e-results.json) `runs[3]` |
| 5 | 8fe3f4b7-bb05-442e-9ade-e2c43f4a195e / none | da73047e-eae7-43fc-8aa1-a5072815c339 / none | [P1](e2e-results.json) `runs[4]`; [P2](e2e-results.json) `runs[4]` |
| 6 | 66c864d0-4838-400d-84d3-17a2e94e3603 / none | 5e59d28c-60e2-4d76-bbb6-a7e153397df3 / none | [P1](e2e-results.json) `runs[5]`; [P2](e2e-results.json) `runs[5]` |
| 7 | e81b70de-0ad5-4bbf-85ea-4177bde2ce93 / none | b4c3d862-106b-4296-9ea8-b59c1c165cf3 / none | [P1](e2e-results.json) `runs[7]`; [P2](e2e-results.json) `runs[7]` |
| 8 | b46e685b-d6bb-484b-89fc-02c58aa4f1ec / a4f2400b-3dd5-4c9c-a8bf-554ea4182ceb | bc04644f-fd12-49e4-bc8d-d7f04b9b8f8f / debc7460-0ef2-4cda-9084-4ddc87a5190d | [P1](e2e-results.json) `runs[6]`; [P2](e2e-results.json) `runs[6]` |

All eight paths match across passes on scenario, status, AI/decision/report/terminal
counts, human-version/modify counts and original snapshot consistency (8/8 logical
outcomes). Generated IDs and timestamps differ. The low-risk report hash matched;
other reports have independently generated IDs. Path 8 asserts within-run report
byte stability.

Runtime acceptance for this historical Task 3 snapshot is complete. It does not
state the current Docker, hosted CI, or delivery status; use the acceptance entry
linked at the top of this document.

Run sequence for each of two fresh databases: set Mock/Fake/no-tracing values,
create with `scripts/seed_e2e.py create --record <owned record> --run-id <32 hex>`;
set the same database as `APP_POSTGRES_DB`, `POSTGRES_DB`, and `E2E_DB_NAME`;
set `PYTHONPATH` to the repository root, `apps/api/src`, and `scripts` for
local Python entry points;
start PostgreSQL, API, the guarded local normal worker using
`python scripts/e2e_runtime.py worker --record <owned record> --database <owned name> --phase normal --proof <new local proof file>`,
and local Vite Web. Set `E2E_OWNERSHIP_RECORD`, `E2E_WORKER_PROOF`, `E2E_DB_NAME`,
`E2E_PYTHON`, `E2E_BASE_URL`, `E2E_PHASE=normal`, and the existing demo-account
password variables for Playwright. The suite verifies the owned record,
Mock/Fake/no-tracing settings, live worker proof, and a unique public API probe
account on that DB before an investigation write. Use a fresh proof file for
each worker launch; remove a stale proof only after confirming its worker has
stopped. Run
`pnpm test:e2e --grep-invert 'path 7'` with `E2E_PHASE=normal`. Stop the
normal worker and start the dedicated replacement with the same record and
fresh proof path; run
`pnpm test:e2e --grep 'path 7'` with `E2E_PHASE=invalid` and one named
`E2E_CITATION_MUTATION`. Stop application processes, then use the recorded
`scripts/seed_e2e.py drop --record <owned record>`. Compare the retained
snapshot with `.superpowers/sdd/stage-6-retained-baseline.json` after each
pass. Screenshots are failure-only; traces/videos stay off.

The simpler approach is to reuse the existing synthetic companies and public
routes. The separate worker command is needed only to test invalid model
output through the real queue and validation boundary.
