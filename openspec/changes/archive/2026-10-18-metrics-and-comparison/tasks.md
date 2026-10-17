# Tasks

## 1. Pricing and schemas

- [x] 1.1 Add `components/metrics/pricing.py` (validated pricing file schema with `Decimal` rates, lazy mtime-cached loader, exact requested-model lookup, pure cost function, `PricingConfigError` naming the file), the optional `PRICING_FILE` setting in `components/core/config.py` and `.env.example`, and `docs/pricing.example.json` with marked example values, and verify unit tests for the cost formula, null token handling, unpriced model, missing file, invalid file (negative rate, unknown key) and cache reload on file change, plus `tests/test_config.py` passing
- [x] 1.2 Add `components/metrics/schemas.py` (summary, per-target comparison, latency, token coverage, evaluator metrics, cost, per-case and page schemas) and verify unit tests that unknown values serialize as null and that no schema carries prompts or responses except the explicit response fields

## 2. Repository and service

- [x] 2.1 Add `MetricsRepository` with the grouped aggregates (status counts and error kinds, latency and token aggregates with `percentile_cont`, evaluator outcomes per target, case page and per-case results) and verify integration tests on seeded data that assert exact counts, error rate inputs, min/mean/median/p95/max, token sums with coverage, evaluator counts, exclusion of cancelled and pending, and targets without results
- [x] 2.2 Add `MetricsService` (summary, comparison with error rate, pass rate and estimated cost, per-case view with optional responses, `pricing_error` degradation) and verify tests for the spec scenarios including 0.25 error rate, 0.75 pass rate, partial token coverage, the 2.00 cost example, unpriced model, no pricing file, invalid pricing file keeping other metrics, and unknown experiment

## 3. Export

- [x] 3.1 Add `components/metrics/export.py` (keyset-batched row iteration, streaming JSON and CSV generators, per-evaluator columns, empty cells for unknowns, CSV formula-injection prefixing) and verify tests that parse both formats back, cover more than one batch, quoting of commas, quotes, newlines and non-ASCII, formula-prefixed cells, the finished flag and a running-experiment export

## 4. API

- [x] 4.1 Add `api/v1/endpoints/metrics.py` (`summary`, `comparison`, `cases`, `export`), register the router, and verify API tests for 200, 404, 422 (bad format, bad pagination, bad boolean), response-text inclusion rules, export content types and `Content-Disposition`, and consistency between comparison and the stored rows
- [x] 4.2 Verify with a test that prompts, responses and expected outputs never appear in captured logs during metrics and export requests, including a hostile response

## 5. Documentation and final checks

- [x] 5.1 Document the endpoints with `curl` examples, metric definitions (error rate, latency, percentiles, token coverage, pass rate), the pricing file format and cost caveats, export formats and CSV prefixing in README.md, and verify the documented commands against a running API with a scratch database and a seeded experiment
- [x] 5.2 Run `pytest` (including integration tests), `ruff check .`, `black --check .` and `mypy` and verify all pass, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-6 (`xkwamfh39nsw`) to In-progress when work starts and to Review once the reviewer agent has reviewed it (see the `kan-review` skill).
- Work on branch `feat/metrics-and-comparison` (stacked on `feat/deterministic-evaluation`), commit with the `git-conventional-commits` skill, do not push without being asked, and archive the change after approval.
