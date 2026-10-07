# Stage 6 automatic evaluation

Run ID: `62e8055643054a57ba54a3ebfea9c484`
Status: `success`
JSON SHA-256: `b010befe2b70eda65b03b46749b2ab503fe071b8b53dbe92cee6d6ca2038f508`

Mode: Mock model, Fake embedding. Real model: not run.

## Required tests

| Group | Exit | Passed | Failed | Errors |
|---|---:|---:|---:|---:|
| test_rules.py | 0 | 7 | 0 | 0 |
| test_retrieval_recall.py | 0 | 20 | 0 | 0 |
| test_citations.py | 0 | 6 | 0 | 0 |
| test_workflow_recovery.py | 0 | 2 | 0 | 0 |

## Metrics

| Metric | Numerator | Denominator | Value | Status |
|---|---:|---:|---:|---|
| rule_exact_match_accuracy | 13 | 13 | 1.000000 | ok |
| rule_business | 4 | 4 | 1.000000 | ok |
| rule_judicial | 3 | 3 | 1.000000 | ok |
| rule_news | 3 | 3 | 1.000000 | ok |
| rule_intellectual_property | 3 | 3 | 1.000000 | ok |
| macro_mean_recall_at_5 | 16.0 | 16 | 1.000000 | ok |
| citation_legality | 2 | 2 | 1.000000 | ok |
| evidence_coverage | 1 | 1 | 1.000000 | ok |
| structured_output_success | 2 | 2 | 1.000000 | ok |

Recall@5 is the existing per-case macro mean. Fake results are a deterministic pipeline baseline, not semantic-model quality.

Database cleanup: `dropped`.
