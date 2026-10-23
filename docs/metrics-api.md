## Metrics and comparison API

[Back to the README](../README.md)

Read-only views over the stored results of an experiment. Nothing is
persisted: every number is computed from the committed rows at request time,
so a running experiment gives a snapshot (`finished` is `false` while any
result is `pending` or `running`). Unknown values are `null`, never zero.
Measured values (counts, latency, tokens) are kept apart from the estimated
cost.

```bash
B=localhost:8000/api/v1/experiments/<experiment_id>

# results by status, experiment status and the finished flag
curl -H "Authorization: Bearer $TOKEN" $B/summary

# per-target comparison: counts, error rate and kinds, latency, tokens,
# evaluator pass rates and estimated cost
curl -H "Authorization: Bearer $TOKEN" $B/comparison

# per-case view (limit 1-100, default 50; offset >= 0; response text only
# with include_responses=true, default false)
curl -H "Authorization: Bearer $TOKEN" "$B/cases?limit=20&offset=0&include_responses=true"

# export: format=json (default) or csv; include_responses defaults to true
# for json and false for csv
curl -H "Authorization: Bearer $TOKEN" -OJ "$B/export?format=json"
curl -H "Authorization: Bearer $TOKEN" -OJ "$B/export?format=csv&include_responses=true"
```

An unknown experiment returns `404` (also for `export`, before anything is
streamed); a bad `limit`, `offset`, `format`, boolean or id returns `422`.

### Metric definitions

Per target (`provider` + `model`), from the frozen configuration, so a target
without results still appears with zeros and `null`s:

- Counts: results per status. `error_rate = failed / (succeeded + failed)`;
  `cancelled`, `pending` and `running` are excluded, and the rate is `null`
  when that denominator is 0. `error_kinds` counts failed results per stored
  `error_kind` (`unknown` when none was stored).
- Latency (milliseconds): over succeeded results with a measured `latency_ms`
  (the adapter-measured duration of the successful provider call, without
  queueing or retry backoff). `count` is the sample size; `min_ms`, `max_ms`,
  `mean_ms` (`avg`), `median_ms` and `p95_ms` come from PostgreSQL
  `percentile_cont`, which interpolates linearly between neighbouring values.
  With few samples p95 is close to the maximum.
- Tokens: `total` sums a field over succeeded results that reported it, and
  `reported_count` says how many did; `tokens.succeeded` is the denominator.
  Coverage below that means the sum understates real usage. A missing count
  is never treated as 0 and `total_tokens` is never computed as input plus
  output.
- Evaluators: `pass_rate = passed / (passed + failed)` per evaluator and
  target; `skipped` and `error` are counted separately and excluded from the
  rate. There is no weighting and no overall score.
- Cost: an estimate, see below.

### Pricing and estimated cost

No prices are shipped. Costs exist only when `PRICING_FILE` points at a JSON
file you maintain (see `docs/pricing.example.json`):

```json
{"version": "2025-01-prices", "currency": "USD",
 "rates": {"openai": {"<requested model id>": {
     "input_per_million": 1.00, "output_per_million": 2.00}}}}
```

`rates` is keyed by provider, then by the requested model id (exact match,
no fuzzy matching). Rates are non-negative numbers per million tokens; unknown
keys are rejected. The example file contains placeholder numbers, not real
prices: verify every rate against the provider's price list before relying on
any cost.

- Per target, `estimated_cost = sum(input_tokens * input_rate / 1e6 +
  output_tokens * output_rate / 1e6)` over succeeded results that reported
  both token counts; `results_included` and `results_excluded` say how many
  results were or were not used. Cost is `null` when none qualifies, `priced`
  is `false` for a model without a rate, and cost is not stored.
- The response and the export state `pricing.version`, the currency and the
  exact rates used. The file is re-read when it changes (no restart), and
  historical costs are recomputed from it, so change `version` whenever you
  change rates.
- A missing or invalid file never fails the request: costs become `null` and
  `pricing_error` gives the reason (never the file path or contents; the path
  is written to the server log).

### Export formats

- JSON is one document streamed in batches: `experiment` (frozen prompt
  template, generation and retry settings, targets, evaluator configuration),
  `status`, `finished`, `comparison` (including the pricing used) and a
  `results` array with one entry per result (case position, target, status,
  attempts, latency, tokens, error kind, `response_truncated`, evaluator
  outcomes and, with
  `include_responses`, `rendered_prompt` and `response_text`). Text is
  preserved exactly.
- CSV has a header and one row per result: `case_position, provider, model,
  status, attempts, latency_ms, input_tokens, output_tokens, total_tokens,
  error_kind, response_truncated` (`true` or `false`), one `eval:<evaluator id>` column per configured evaluator, and
  `response_text` when requested. Unknown values are empty cells.
- CSV formula protection: a text cell (provider, model, status, error kind,
  evaluator outcome, response text) starting with `=`, `+`, `-`, `@`, a tab or
  a carriage return gets a leading single quote, so spreadsheets do not run
  model output as a formula. This alters those cells; use JSON when the exact
  text matters.
- Exports work in any status; check `finished` to tell partial exports from
  final ones. The filename is `experiment-<experiment_id>.json|csv`.
- Limitation: the status line is sent before the rows are read. If the
  database fails mid-export the server logs the failure (id, format and row
  count only) and ends the stream early with status 200: truncated JSON does
  not parse, and for CSV compare the row count with `summary.counts.total`.
