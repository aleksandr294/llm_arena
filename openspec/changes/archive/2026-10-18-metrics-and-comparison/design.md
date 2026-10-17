# Design

## Context

Stored per result (`experiment_results`): provider, requested/reported model, status, attempts, measured `latency_ms` (adapter-measured duration of the successful provider call, excluding queueing and backoff), `input_tokens`/`output_tokens`/`total_tokens` (nullable, as reported), `error_kind`, rendered prompt and response text. Evaluator outcomes live in `evaluation_results`. The experiment's frozen config holds targets and evaluator configuration. Experiments are capped at 10 000 results. CLAUDE.md requires measured and estimated values to be distinguished, unknowns to be null, and the pricing version or assumptions to be stored with estimated costs. See proposal.md and the specs.

## Goals / Non-Goals

**Goals:**
- Reported numbers are exactly what the stored rows imply, computed in the database where possible.
- No invented values: no shipped prices, no zero for unknown, no derived totals.
- Exports are bounded in memory and safe to open in spreadsheets.

**Non-Goals:**
- Cross-experiment comparison, ranking, composite scores, persisted reports, frontend (see proposal).

## Decisions

- **Layout:** `components/metrics/{pricing,schemas,repository,service,export}.py` and `api/v1/endpoints/metrics.py`; tests in `tests/metrics/`. No `models.py` and no migration: this is a read-only component over experiments and evaluation tables. `MetricsRepository` holds the SQL aggregates and reuses the existing models.
- **Endpoints** (all under `/api/v1/experiments/{experiment_id}`): `GET /summary`, `GET /comparison`, `GET /cases` (limit, offset, `include_responses=false`), `GET /export?format=json|csv&include_responses=true|false`. Unknown experiment is 404, bad query values are 422.
- **Metric definitions (documented in README):**
  - `error_rate = failed / (succeeded + failed)`; cancelled, pending and running are excluded; null when the denominator is 0.
  - Latency statistics use succeeded results with non-null `latency_ms`; mean via `avg`, median and p95 via PostgreSQL `percentile_cont` (continuous interpolation), min and max via aggregates; `latency_count` is reported.
  - Token sums use `SUM` over rows where the field is not null; `reported_count` per field says how many succeeded results contributed, and `succeeded` gives the denominator. No null is coerced to 0 and `total_tokens` is never computed from input plus output.
  - Pass rate per evaluator = `passed / (passed + failed)` per target; `skipped` and `error` are reported separately and excluded from the rate. No weighting or overall score.
- **Query shape:** one grouped query per metric family keyed by `(provider, model)`: status counts and error kinds, latency and token aggregates (filtered `FILTER (WHERE ...)`), evaluator outcomes joined from `evaluation_results` on `experiment_result_id` grouped by `(provider, model, evaluator_id, status)`. All filtered by `experiment_id` using the existing `(experiment_id, status)` index; at most 10 000 rows per experiment makes this cheap. Targets come from the frozen config so a target without results still appears with zeros and nulls.
- **Pricing:** `PRICING_FILE` (optional path in settings) points to an operator-supplied JSON `{"version": str, "currency": str, "rates": {"<provider>": {"<model>": {"input_per_million": number, "output_per_million": number}}}}` validated with Pydantic (non-negative, `Decimal`, unknown keys rejected). Rates are matched on the requested model id (exact). Nothing is shipped as fact: `docs/pricing.example.json` carries clearly marked example numbers and a note to verify against provider price lists. Loading is lazy and cached per process with the file's mtime as cache key, so editing the file takes effect without a restart; an invalid file raises `PricingConfigError` naming the file path (never its contents). Cost fields degrade instead of failing the whole response: `estimated_cost` is null and a top-level `pricing_error` message (file name and reason) is returned, so the other metrics stay available.
- **Cost computation:** per succeeded result with both `input_tokens` and `output_tokens` known, `cost = in * input_rate/1e6 + out * output_rate/1e6` in `Decimal`; the target cost is the sum, reported as a string-number with the number of results included and excluded for missing tokens. It is computed at request time, never stored. Trade-off: editing the pricing file changes historical numbers, so every response and export states the pricing version, currency and the exact rates used; operators should bump `version` when rates change.
- **Per-case view:** paginated by case position (from `experiment_results.case_position`): select the page of distinct positions, then load that page's results and evaluation outcomes in two queries and assemble in the service. `include_responses=false` by default keeps payloads small; the case input is not included (the rendered prompt is not either; the export has it).
- **Export:** `export.py` provides two generators over keyset-paginated batches (by `case_position, provider, model, id`, batch of 500) fed to a `StreamingResponse` with `Content-Disposition` filenames derived from the experiment id only. JSON export streams a document: header object (experiment config, status, finished flag, comparison, pricing info) followed by a `results` array written incrementally. CSV has one column per configured evaluator (`eval:<id>`), unknown values as empty cells, and standard quoting through the `csv` module. Text cells beginning with `=`, `+`, `-`, `@`, tab or carriage return get a leading single quote. `include_responses` defaults to true for JSON and false for CSV. Because the comparison needs the whole experiment, it is computed once before streaming starts.
- **Consistency:** metrics read committed rows at request time under READ COMMITTED; a running experiment's numbers are a snapshot per query, and the summary/export expose `finished` so partial data is labeled. No locks are taken.
- **Untrusted data and logging:** responses and rendered prompts are only passed through `json`/`csv` encoders, never interpreted; logs carry experiment id, format and row counts only.
- **Tests:** repository integration tests that seed a known set of results and assert exact aggregates (including nulls, cancelled/pending exclusion, percentiles on known values, zero-result targets); pure unit tests for cost and pricing validation; service tests for composition; API tests; export tests that parse CSV/JSON back, check formula prefixing, batching across more than one batch, and a running experiment export.

## Risks / Trade-offs

- [Costs recomputed from a mutable file] → version, currency and rates in every response/export; documented practice to bump the version.
- [Operator forgets the pricing file] → cost is null with `pricing_version` null, never a guess.
- [Aggregates on large experiments] → bounded by the 10 000 result cap and the existing index; revisit with materialized snapshots if experiments grow.
- [Percentile interpolation may surprise] → definition documented; `latency_count` shows the sample size.
- [CSV prefix alters the original text] → documented; JSON export keeps exact text.
- [Streaming errors mid-export] → status 200 already sent; the generator logs the failure and ends the stream without a trailer, so consumers must check JSON validity / row counts. Documented limitation.

## Open Questions

- Whether prices should later be stored per experiment at creation for strict reproducibility; deferred to a future change if needed.
