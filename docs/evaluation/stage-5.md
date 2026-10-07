# Stage 5 acceptance evaluation

Stage 5 is complete after the final independent integrated review returned **READY** with P1 0, P2 0, and P3 0. The review's P2 SSE generation race and P3 package-byte digest findings were corrected and independently rechecked. Stage 6 has not begun.

The follow-up adds executable Run-switch, scope-disposal, stale `stream.ready`, and late-response regressions to the Stage 5 frontend boundary runner. Fresh Stage 5, auth, and business tests, lint, typecheck, build, and API drift check all passed. The package generator now hashes saved bytes, and regenerated package/manifest/scan hashes were verified. This follow-up did not repeat the backend suite, retained browser run, migrations, or database writes; the original non-green full-suite record below remains unchanged.

## Preserved data and migration

The retained `tracerisk` database was frozen at 0010 before writes, with every table's primary keys and canonical row hashes, pgvector/index/query identity, old report hashes, frozen retrieval replay and named volume identity. Backup `retained-0010.backup` is 234008 bytes (SHA-256 `c0ef06697e74f711473c4bec8202e9ca0693df46524936e988528c04228991ac`). It was restored only to `tracerisk_stage5_task7_rehearsal_20260926`; rehearsal migration and exact original-row comparison passed before retained upgrade. Retained revision is 0011; its migration-only comparison passed with an empty event table. The named volume `tracerisk_postgres_data` was preserved. The rehearsal database and backup are retained for review, without cleanup or destructive retained commands.

The final read-only comparison found no changed/missing original rows and no table changes since the authorized Run acceptance. Counts, primary-key sets and canonical hashes are in baseline, rehearsal-preservation, retained-preservation, acceptance-preservation and final-preservation JSON artifacts under `.superpowers/sdd/stage-5-task-7/`.

## One authorized Run

Run `2a32e7f2-ada6-4318-a893-1f911b624c63` completed via public API, Outbox and real worker start/resume. Exactly 13 increasing durable events cover queued/running/nodes/awaiting-review/resume/report/completion. History, live delivery, reconnect after sequence 1, awaiting heartbeat, terminal history drain/high-water 13, immutable report and safe redelivery all passed. Four queue deliveries succeeded without duplicate events or reports. Existing report snapshots were not rewritten. No second Run was created. The initial harness used the wrong terminal-control field name; a read-only follow-up verified `high_water_mark` against the same Run.

## Validation

Exactly one complete backend invocation: `pytest -p no:cacheprovider -q` — **341 passed, 2 failed, 16 setup errors, 1 warning**, 518.54 seconds, exit 1. The two stale migration fixtures were corrected for Stage 5: construct the historical Investigation/Run fixture directly at revision 0009 instead of calling the modern event-writing creation service, and expect head 0011 after refused downgrade. The 16 label-validation setup errors were sandbox temporary-directory ACL failures. Focused rerun with a workspace basetemp passed all **18** affected cases in 2.59 seconds. No second full suite was run and no full-suite-green claim is made.

Ruff and formatting passed. Initial mypy could not read installed pgvector due to sandbox ACL; narrow escalated strict `mypy --no-incremental src` passed, **86 source files**. Frontend auth/business/Stage 5 boundary tests, lint, typecheck, build and API drift check passed. Alembic upgrade/current/check passed at 0011 with no new upgrade operations.

Warnings remain: Starlette uses deprecated AnyIO BlockingPortal alias; Vite reports its 651.56 kB bundle over 500 kB. Neither was changed in this scope.

## Retained browser acceptance

Real retained-stack seven-page acceptance passed with three roles, modern report and immutable historical report/Run, 13-event terminal timeline, exact citation focus and delayed return, desktop/mobile screenshots, no page errors and no attempted business writes. The 14-case Task 6 forced-state matrix was repeated on the same web/API stack and passed. Synthetic negative states intercept requests; they do not mutate the retained database. Screenshots and machine results are under `stage-5-task-7/browser/` and `browser-retained-results.json`.

The first browser attempt lacked Playwright in the API environment; the existing installed Playwright was reused without installing dependencies. Its failed log is preserved alongside the passing follow-up. The lifecycle helper left server children; exact command identities were rechecked immediately before targeted termination. No unrelated process or retained volume was terminated. Model execution stayed mock and loopback-only.

## Checklist and review boundary

Exactly ten existing Stage 5 boxes record evidence-backed implementation checks. Final independent integrated review is READY. Stage 6 bytes have SHA-256 `b45e232aca286df9bf133eda7d5fac174485168e808b2029630977557acb45d7` matching the pre-write baseline and zero checked boxes. The numbered final review package includes source, tests, reports, evaluation, review and ledger. Backup binary and raw secret-bearing configuration are excluded.
