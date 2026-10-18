## Experiments API

[Back to the README](../README.md)

An experiment runs every test case of a dataset against one or more model
targets using a prompt template, and stores each case/target pair as its own
result. Creating an experiment makes no model request: it freezes the case
set and the exact rendered prompt of every result (`pending`). Later dataset
edits do not change an existing experiment. Comparison and cost estimates are described
under the metrics API below; deterministic evaluation is described below.

```bash
# create (201). {input} is required in prompt_template; {expected_output}
# is optional. Each target's provider needs its API key configured (else 422).
curl -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/experiments \
    -H 'Content-Type: application/json' \
    -d '{"name": "capitals-v1", "dataset_id": "<dataset_id>",
         "prompt_template": "Answer briefly: {input}",
         "config": {"targets": [{"provider": "openai", "model": "<model>"},
                                {"provider": "anthropic", "model": "<model>"}],
                    "system_prompt": "Be concise.", "temperature": 0,
                    "max_tokens": 256, "timeout_seconds": 60,
                    "max_attempts": 3}}'

# list, detail (config + status_counts) and results (limit 1-100, offset,
# optional status filter: pending|running|succeeded|failed|cancelled)
curl -H "Authorization: Bearer $TOKEN" 'localhost:8000/api/v1/experiments?limit=10'
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/experiments/<experiment_id>
curl -H "Authorization: Bearer $TOKEN" 'localhost:8000/api/v1/experiments/<experiment_id>/results?status=failed&limit=20'

# start (202): enqueue the pending results for the worker
curl -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/experiments/<experiment_id>/start

# cancel (200) and retry only the failed results (202)
curl -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/experiments/<experiment_id>/cancel
curl -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/experiments/<experiment_id>/retry-failed
```

`start` and `retry-failed` return `{"experiment": {...}, "enqueued": N}`.
`202` and `enqueued` mean the tasks were submitted, not that anything ran:
poll the experiment detail and its `status_counts`. Errors: unknown
experiment or dataset `404`; an action not allowed in the current status
`409`; invalid input `422` with `detail` entries that name the field in
`loc` (for example `["body", "config", "targets"]`).

Execution needs a running worker (see Run). Limits: at most 10 targets, at
most 10 000 results (cases times targets) per experiment, `max_attempts`
1-5, `timeout_seconds` above 0 and at most 120.

### Status model

- Result: `pending -> running -> succeeded | failed`; `pending -> cancelled`;
  `failed -> pending` on `retry-failed`.
- Experiment: `pending -> running -> completed | partially_failed | failed`;
  `pending | running -> cancelled`; `partially_failed | failed -> running`
  on `retry-failed`. `completed` and `cancelled` are final. The final status
  is written when the last result finishes: `completed` (all succeeded),
  `partially_failed` (some succeeded), `failed` (none succeeded).
- Stored metrics are measured values only (tokens as reported by the
  provider, latency measured by the adapter); a missing value is `null`, not
  zero.

### Cancellation and retry

- `cancel` marks pending results `cancelled` immediately. Results already
  running finish and are stored, then the experiment becomes `cancelled`.
  Cancelling a final experiment returns 409. A cancelled experiment keeps
  the status `cancelled` even when some of its results succeeded or failed;
  see `status_counts` for the breakdown. It cannot be
  restarted or retried.
- `retry-failed` re-runs only failed results (successful ones are untouched)
  and returns 409 unless the experiment is `partially_failed` or `failed`.
- `start` on a `running` experiment re-enqueues its still-pending results
  and its stale `running` results (running longer than the 1800 second
  lease, left by a crashed worker). This recovers from lost messages and
  crashes: call `start` again. Duplicate delivery is safe: a result is
  claimed atomically, and one that is already finished or owned is skipped.
  A worker whose lease was reclaimed cannot overwrite the new outcome.
- A retryable provider error (timeout, rate limit, server error) is retried
  inside the task with exponential backoff (capped at 30 seconds) up to
  `max_attempts`; other errors fail immediately. `error_kind` and a generic
  `error_message` are stored; provider error text, prompts and API keys are
  never logged or stored in error fields.

### Bounded rendered prompts

A template may repeat `{input}` many times, so the rendered prompt can be far
larger than any single field. Creating an experiment is rejected with `422`
(field `prompt_template`, nothing stored) when any one rendered prompt would
exceed 200000 characters, or all rendered prompts together would exceed
100000000 characters. The sizes are computed from the template and the case
texts before any prompt is built, so a hostile request cannot exhaust memory.
These limits are constants (`MAX_RENDERED_PROMPT_CHARS`,
`MAX_TOTAL_RENDERED_CHARS` in `components/experiments/utils.py`), not
settings: they protect the service itself and a template with maximal
`{input}` and `{expected_output}` fields still fits.

### Bounded stored output

`MAX_STORED_RESPONSE_CHARS` (default 1000000) bounds the stored
`response_text` per result, counted in characters. A longer response is cut so
that the stored text, including the visible marker `\n[... response
truncated ...]` at its end, is exactly that many characters, and the result
reports `response_truncated: true` (also in the JSON and CSV exports). A
response within the limit is stored unchanged with `response_truncated:
false`. Results stored before this setting existed read `false`. The original
length is logged, never the text. Evaluators and metrics see the stored
(truncated) text.

### Worker concurrency

`LLM_MAX_CONCURRENCY` (default 4) bounds simultaneous model requests per
provider inside one worker process. It is per process: total in-flight
requests are roughly worker processes times this value, and there is no
global limit across workers. Taskiq's own parallelism is set with worker
options, for example `taskiq worker ... --workers 2 --max-async-tasks 20`;
backoff sleeps hold a task slot but not a request slot.
