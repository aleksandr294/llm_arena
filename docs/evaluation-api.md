## Evaluation API

[Back to the README](../README.md)

Evaluation judges stored responses with deterministic checks. It is separate
from generation: an evaluation never changes a result's text or status, and
no model is called. Evaluators run in the worker (`evaluation.evaluate_result`,
one task per succeeded result) right after a result succeeds, and their
outcomes are stored as one row per result and evaluator. Failed or cancelled
generations are not evaluated.

### Evaluator configuration

Pass `evaluator_config` when creating an experiment:

```json
{"evaluators": [
  {"id": "exact", "type": "exact_match", "case_sensitive": false},
  {"id": "no_apology", "type": "not_contains", "values": ["sorry"]},
  {"id": "shape", "type": "json_fields", "required": ["answer"],
   "types": {"answer": "string"}}
]}
```

`id` is unique within the experiment (1-64 characters) and names the
evaluator in results; `type` selects the evaluator. At most 20 entries. `{}`,
or an omitted field, or an empty list means no evaluation. Unknown
parameters are rejected. The stored configuration has every default filled
in (the detail response shows it), so later changes to a default do not
alter an existing experiment; each stored outcome also records the evaluator
`type` and `version`.

Every outcome is `passed`, `failed`, `skipped` (the check does not apply) or
`error` (the evaluator itself raised; only the exception class name is
stored). `details` hold a machine-readable `reason` and configured values'
indexes or paths, never response text.

| type | parameters (defaults) | outcomes and details |
| --- | --- | --- |
| `exact_match` | `trim` (true), `case_sensitive` (true), `collapse_whitespace` (false) | compares with the case's `expected_output` (`collapse_whitespace` also removes leading and trailing whitespace, even with `trim` false); `skipped` (`no_expected_output`) without one; else `passed` (`match`) or `failed` (`mismatch`) |
| `contains` | `values` (1-20 strings, required), `mode` (`all` or `any`, default `all`), `case_sensitive` (true) | `passed` (`all_found`, `found`) or `failed` (`missing_values` with `missing_indexes`, `none_found`) |
| `not_contains` | `values` (1-20 strings, required), `case_sensitive` (true) | `passed` (`none_found`) or `failed` (`forbidden_values_found` with `found_indexes`) |
| `length_between` | `min_length`, `max_length` (characters; at least one required) | `passed` (`in_range`) or `failed` (`too_short`, `too_long`), with `length` |
| `json_valid` | `require_object` (false) | the stripped response must be strict JSON (no `NaN`); `failed` reasons `invalid_json`, `contains_nul`, `too_deep`, `not_object` |
| `json_fields` | `required` (dot paths), `types` (dot path to `string`, `number`, `integer`, `boolean`, `array`, `object` or `null`); one of them is required | the response must be a JSON object; `failed` with the lists `missing` and `mistyped`, or the `json_valid` reasons. Paths walk object keys only; booleans are not numbers |

Invalid configuration returns 422 with the path in `loc`, for example
`["body", "evaluator_config", "evaluators", 1]` for an unknown type,
`["body", "evaluator_config", "evaluators"]` for a duplicate id, and
`["body", "evaluator_config", "evaluators", 0, "contains", "values"]` for an
empty `values` (the evaluator type appears in the path of its parameters).
An experiment whose stored configuration predates this feature and no longer
parses is treated as having no evaluators (evaluation is disabled for it and
only a warning is logged). A succeeded result without stored response text
gets an `error` outcome (`missing_response`) per evaluator.

### Endpoints

```bash
# backfill (202): enqueue evaluation of succeeded results that lack an
# outcome for some configured evaluator
curl -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/experiments/<experiment_id>/evaluate

# outcomes (limit 1-100, default 50; offset >= 0; optional evaluator_id and
# status filters: passed|failed|skipped|error)
curl -H "Authorization: Bearer $TOKEN" 'localhost:8000/api/v1/experiments/<experiment_id>/evaluations?evaluator_id=exact&status=failed&limit=20'

# per-evaluator outcome counts
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/experiments/<experiment_id>/evaluations/summary
```

`evaluate` returns `{"experiment_id": "...", "enqueued": N, "remaining": M}`:
at most 1000 results are enqueued per request (in case order), and
`remaining` is how many still lack an evaluation after this batch, so a large
backlog is processed by calling it again. As with
`start`, `202` means the tasks were submitted, not that they ran: it needs a
running worker, and the outcome is visible through the other two endpoints.
Backfill is for missed triggers (a lost message, an experiment evaluated
before its worker was up); it is idempotent, so repeating it or duplicate
delivery never creates a second row or changes a stored outcome, and it
returns `enqueued: 0` when nothing is missing or no evaluators are
configured. Results already evaluated are not recomputed. List responses
have the form `{items, total, limit, offset}`, ordered by case position,
target and evaluator id; an item has `id`, `experiment_result_id`,
`evaluator_id`, `evaluator_type`, `evaluator_version`, `status`, `details`
and `created_at`, and never the response text (read that from the results
endpoint). The summary is
`{"evaluators": [{"id", "type", "version", "passed", "failed", "skipped",
"error"}], "unevaluated_results": N}` with explicit zeros;
`unevaluated_results` is the number of succeeded results still lacking an
outcome for at least one configured evaluator, so a non-zero value means
evaluation is pending or `evaluate` is needed. Unknown experiment `404`;
invalid pagination, filter value or id `422`.

Not included: regular expressions, JSON Schema validation, LLM-as-a-judge,
and overall scores (per-evaluator pass rates are in the metrics API).
