# LLM Arena for Developers

Backend platform for evaluating and comparing LLM providers and models. See
`CLAUDE.md` for architecture and conventions.

## Setup

```bash
poetry install
cp .env.example .env          # replace placeholder credentials and set
                              # JWT_SECRET_KEY (see Authentication)
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
edits do not change an existing experiment. Comparison and cost estimates are described
under the metrics API below; deterministic evaluation is described below.

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
and overall scores (per-evaluator pass rates are in the metrics API).

## Metrics and comparison API

Read-only views over the stored results of an experiment. Nothing is
persisted: every number is computed from the committed rows at request time,
so a running experiment gives a snapshot (`finished` is `false` while any
result is `pending` or `running`). Unknown values are `null`, never zero.
Measured values (counts, latency, tokens) are kept apart from the estimated
cost.

```bash
B=localhost:8000/api/v1/experiments/<experiment_id>

# results by status, experiment status and the finished flag
curl $B/summary

# per-target comparison: counts, error rate and kinds, latency, tokens,
# evaluator pass rates and estimated cost
curl $B/comparison

# per-case view (limit 1-100, default 50; offset >= 0; response text only
# with include_responses=true, default false)
curl "$B/cases?limit=20&offset=0&include_responses=true"

# export: format=json (default) or csv; include_responses defaults to true
# for json and false for csv
curl -OJ "$B/export?format=json"
curl -OJ "$B/export?format=csv&include_responses=true"
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
  `pricing_error` names the file path and the reason (never its contents).

### Export formats

- JSON is one document streamed in batches: `experiment` (frozen prompt
  template, generation and retry settings, targets, evaluator configuration),
  `status`, `finished`, `comparison` (including the pricing used) and a
  `results` array with one entry per result (case position, target, status,
  attempts, latency, tokens, error kind, evaluator outcomes and, with
  `include_responses`, `rendered_prompt` and `response_text`). Text is
  preserved exactly.
- CSV has a header and one row per result: `case_position, provider, model,
  status, attempts, latency_ms, input_tokens, output_tokens, total_tokens,
  error_kind`, one `eval:<evaluator id>` column per configured evaluator, and
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

## Authentication and users

Users belong to organizations and sign in with email and password. A login
returns a short-lived access token (JWT) and a refresh token; both are
signed with `JWT_SECRET_KEY` (HS256).

### Roles

| role | organization | can do |
| --- | --- | --- |
| `superadmin` | none | everything: manage organizations, create and manage `admin` and `staff` users in any organization. Created only with the CLI below |
| `admin` | exactly one | create and manage `staff` and `admin` users in their own organization |
| `staff` | exactly one | sign in and read their own user record |

Role and active state are read from the database on every request, so a
role change or deactivation applies at once (a deactivated user or
organization cannot sign in, refresh, or use an existing access token).
Deactivating a user or changing their password revokes all their refresh
tokens at once, so no new access token can be obtained. Access tokens that
were already issued stay valid for their remaining lifetime (up to 15
minutes by default) after a password change or logout; only deactivating the
user or their organization stops them immediately, because they are
re-checked against the database.
Superadmins cannot be created, edited or deactivated over HTTP.

### Settings

| setting | default | meaning |
| --- | --- | --- |
| `JWT_SECRET_KEY` | none, required | signing key, at least 32 characters; the value from `.env.example` is rejected at startup |
| `JWT_ACCESS_TTL_SECONDS` | 900 (15 minutes) | access token lifetime |
| `JWT_REFRESH_TTL_SECONDS` | 604800 (7 days) | refresh token lifetime |
| `JWT_ISSUER` | `llm-arena` | `iss` claim, checked on every token |

Generate a key and never commit it:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Changing `JWT_SECRET_KEY` invalidates every issued token (all users must
sign in again).

### Create the first superadmin

There is no HTTP endpoint for this. Run the CLI on a migrated database. The
password is never an argument: it comes from `SUPERADMIN_PASSWORD` or from a
hidden prompt (asked twice). The command prints only the new user id.

```bash
python cli.py create-superadmin --email root@example.com        # prompts
SUPERADMIN_PASSWORD='...' python cli.py create-superadmin --email root@example.com
```

Emails are compared ignoring case; a duplicate or a weak password (policy:
at least 12 characters) is refused with exit code 1.

### Sign in, refresh, sign out

```bash
B=localhost:8000/api/v1

# login -> {access_token, refresh_token, token_type: "bearer", expires_in}
curl -X POST $B/auth/login -H 'Content-Type: application/json' \
    -d '{"email": "root@example.com", "password": "<password>"}'

# call a protected endpoint
curl $B/organizations -H "Authorization: Bearer <access_token>"

# validate any access token: always 200, {"valid": true, user_id,
# organization_id, role, expires_at} or {"valid": false, "reason": ...}
curl -X POST $B/auth/validate -H 'Content-Type: application/json' \
    -d '{"token": "<access_token>"}'

# refresh: returns a new pair and retires the old refresh token
curl -X POST $B/auth/refresh -H 'Content-Type: application/json' \
    -d '{"refresh_token": "<refresh_token>"}'

# logout (204): ends the whole login (the token's family)
curl -X POST $B/auth/logout -H 'Content-Type: application/json' \
    -d '{"refresh_token": "<refresh_token>"}'
```

Refresh tokens are single use. Presenting a used one again is treated as
theft: the whole login is revoked and the call returns 401, so the newest
token of that login stops working too. There is no grace window by design:
a legitimate client that retries a refresh after losing the response looks
like a reuse and loses the whole login session, so it must sign in again.
Logout accepts a correctly signed refresh token even if it has expired. Wrong credentials, unknown email,
inactive user or organization all give the same 401. A missing, malformed,
expired or revoked access token gives 401 with a `WWW-Authenticate: Bearer`
header; a valid token without the needed role gives 403. Do not use the
`validate` endpoint to learn why a login failed: its `reason` is for token
holders only.

### Organizations (superadmin only)

```bash
H="Authorization: Bearer <access_token>"
J='Content-Type: application/json'

curl -X POST $B/organizations -H "$H" -H "$J" -d '{"name": "Acme"}'   # 201
curl "$B/organizations?limit=10&offset=0" -H "$H"
curl $B/organizations/<organization_id> -H "$H"
curl -X PATCH $B/organizations/<organization_id> -H "$H" -H "$J" \
    -d '{"name": "Acme Inc", "is_active": true}'
```

Names are unique ignoring case (409 on a duplicate). Deactivating an
organization blocks its users from signing in.

### Users

```bash
# superadmin creates an admin (organization_id required)
curl -X POST $B/users -H "$H" -H "$J" \
    -d '{"email": "admin@acme.test", "password": "<password>",
         "role": "admin", "organization_id": "<organization_id>"}'

# an admin creates staff in their own organization (organization_id is
# optional and must match theirs)
curl -X POST $B/users -H "$H" -H "$J" \
    -d '{"email": "staff@acme.test", "password": "<password>", "role": "staff"}'

# list (limit 1-100; organization_id, role, is_active filters), get, update
curl "$B/users?role=staff&is_active=true" -H "$H"
curl $B/users/<user_id> -H "$H"
curl -X PATCH $B/users/<user_id> -H "$H" -H "$J" \
    -d '{"is_active": false}'    # also role (staff|admin) and password
```

Admins see and change only users of their own organization (others look
like 404); staff see only themselves. You cannot change your own role or
deactivate yourself. Responses never contain password hashes.

### Current scope and limitations

- The dataset, experiment, evaluation and metrics endpoints are still open:
  they do not require a token. Protecting them and scoping data by
  organization is the next change (`resource-access-control`).
- Login has no rate limiting or lockout, per account or per IP, so put the
  API behind a proxy that limits `/auth/login` and `/auth/refresh` if it is
  exposed. Only the number of concurrent password hashes is bounded (4 per
  process), which protects memory but does not stop guessing.
- Emails are limited to printable ASCII (no spaces or non-ASCII letters, at
  most 254 characters) so that case-insensitive uniqueness is predictable.
  NUL characters are rejected in emails and organization names (422).
- Access tokens are stateless for their short lifetime but are re-checked
  against the database, so there is no separate access-token revocation
  list.

## Migrations

```bash
alembic upgrade head      # apply
alembic current           # show current revision
alembic revision --autogenerate -m "message"   # review before applying
```

Authentication adds revision `0005` (tables `organizations`, `users`,
`refresh_tokens`): run `alembic upgrade head` before starting the API with
this change, and create the first superadmin afterwards. Roll back with
`alembic downgrade 0004` (this deletes users, organizations and sessions).

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
