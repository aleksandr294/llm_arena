# LLM Arena for Developers

Backend platform for evaluating and comparing LLM providers and models. See
`CLAUDE.md` for architecture and conventions.

## Setup

```bash
poetry install
cp .env.example .env          # replace placeholder credentials
docker compose up -d postgres rabbitmq
set -a; . ./.env; set +a      # export settings for local commands
```

## Run

```bash
python main.py API                       # API on API_HOST:API_PORT
taskiq worker taskiq_app:broker --fs-discover \
    --tasks-pattern "components/*/tasks.py"   # background worker
```

If RabbitMQ is unreachable the worker logs an `AMQPConnectionError` for the
broker URL (password masked) and retries every 5 seconds.

Health endpoints: `GET /api/v1/health/live` (process only) and
`GET /api/v1/health/ready` (PostgreSQL and RabbitMQ; 503 if any is failing).

The whole stack: `docker compose up --build`.

## Datasets API

A dataset is a named, ordered set of test cases. Test cases are append-only:
each gets the next position (1, 2, ...) in its dataset. Content is stored and
returned exactly as supplied.

```bash
# create a dataset (201); name is trimmed, description is optional
curl -X POST localhost:8000/api/v1/datasets \
    -H 'Content-Type: application/json' \
    -d '{"name": "Capitals", "description": "Geography smoke test"}'

# list (limit 1-100, default 50; offset >= 0) and get (includes case_count)
curl 'localhost:8000/api/v1/datasets?limit=10&offset=0'
curl localhost:8000/api/v1/datasets/<dataset_id>

# add a test case (201); expected_output and metadata (object) are optional
curl -X POST localhost:8000/api/v1/datasets/<dataset_id>/cases \
    -H 'Content-Type: application/json' \
    -d '{"input": "Capital of France?", "expected_output": "Paris",
         "metadata": {"topic": "geo"}}'

# list test cases ordered by position
curl 'localhost:8000/api/v1/datasets/<dataset_id>/cases?limit=10'
```

List responses have the form `{items, total, limit, offset}`. Invalid input
returns 422 naming the field; an unknown dataset returns 404.

## Experiments API

An experiment runs every test case of a dataset against one or more model
targets using a prompt template, and stores each case/target pair as its own
result. Creating an experiment makes no model request: it freezes the case
set and the exact rendered prompt of every result (`pending`). Later dataset
edits do not change an existing experiment. Cost and comparison are not part
of this API yet; deterministic evaluation is described below.

```bash
# create (201). {input} is required in prompt_template; {expected_output}
# is optional. Each target's provider needs its API key configured (else 422).
curl -X POST localhost:8000/api/v1/experiments \
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
curl 'localhost:8000/api/v1/experiments?limit=10'
curl localhost:8000/api/v1/experiments/<experiment_id>
curl 'localhost:8000/api/v1/experiments/<experiment_id>/results?status=failed&limit=20'

# start (202): enqueue the pending results for the worker
curl -X POST localhost:8000/api/v1/experiments/<experiment_id>/start

# cancel (200) and retry only the failed results (202)
curl -X POST localhost:8000/api/v1/experiments/<experiment_id>/cancel
curl -X POST localhost:8000/api/v1/experiments/<experiment_id>/retry-failed
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

### Worker concurrency

`LLM_MAX_CONCURRENCY` (default 4) bounds simultaneous model requests per
provider inside one worker process. It is per process: total in-flight
requests are roughly worker processes times this value, and there is no
global limit across workers. Taskiq's own parallelism is set with worker
options, for example `taskiq worker ... --workers 2 --max-async-tasks 20`;
backoff sleeps hold a task slot but not a request slot.

## Evaluation API

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
curl -X POST localhost:8000/api/v1/experiments/<experiment_id>/evaluate

# outcomes (limit 1-100, default 50; offset >= 0; optional evaluator_id and
# status filters: passed|failed|skipped|error)
curl 'localhost:8000/api/v1/experiments/<experiment_id>/evaluations?evaluator_id=exact&status=failed&limit=20'

# per-evaluator outcome counts
curl localhost:8000/api/v1/experiments/<experiment_id>/evaluations/summary
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
and aggregate scores or cross-model comparison.

## Migrations

```bash
alembic upgrade head      # apply
alembic current           # show current revision
alembic revision --autogenerate -m "message"   # review before applying
```

Register each component's `models` module in `migrations/env.py` so
autogenerate sees its tables. The baseline revision `0001` is empty.

## Quality checks

```bash
pytest                    # integration tests need DATABASE_URL (PostgreSQL)
pytest -m "not integration"
ruff check .
black --check .
mypy
pre-commit run --all-files
```

## Real-provider tests

Tests in `tests/llm/real/` call the real OpenAI and Anthropic APIs, cost
money, and are skipped by default. To run them:

```bash
RUN_REAL_PROVIDER_TESTS=1 OPENAI_API_KEY=... ANTHROPIC_API_KEY=... \
  pytest -m real_provider tests/llm/real
```

Each test is also skipped if its provider key is unset. Override the models
with `OPENAI_TEST_MODEL` / `ANTHROPIC_TEST_MODEL`.

## Development agents

Project subagents in `.claude/agents/` support the OpenSpec workflow. Run
them in this order for each task:

1. `coder` implements one task from the change's `tasks.md`.
2. `tester` adds tests and runs pytest, Ruff, Black and mypy.
3. `reviewer` does a read-only review of the diff against the change and
   `CLAUDE.md`.

The agents do not commit, push or merge; that stays with the main session.
