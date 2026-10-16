# Design

## Context

Available: datasets (`Dataset`, append-only `TestCase` with `position`), `LLMService.generate(target, prompt, ...)` raising `LLMProviderError(kind, retryable, retry_after_seconds, request_id)`, `get_session` (commit per request), a Taskiq `broker` with per-component `tasks.py` discovery, and a task-runtime rule that enqueueing is not completion. This change is stacked on `feat/provider-abstraction`; it must be rebased onto `main` once that branch is merged. See proposal.md for motivation and the spec for behavior.

## Goals / Non-Goals

**Goals:**
- Results are the source of truth: every state change is a committed database row, never an in-memory or broker fact.
- Duplicate delivery, worker crash and lost messages are recoverable without duplicating or losing a result.
- A frozen, explainable configuration per experiment.

**Non-Goals:**
- Evaluation, cost, comparison, judges, auth, cross-worker global rate limiting (see proposal).

## Decisions

- **Layout:** `components/experiments/{models,schemas,repository,service,runner,tasks}.py` and `api/v1/endpoints/experiments.py`. `service.py` (`ExperimentService`) owns API-side orchestration: create, start, cancel, retry-failed, reads. `runner.py` (`ExperimentRunner`) executes one result. `tasks.py` is a thin Taskiq wrapper. Splitting runner from service keeps worker logic free of HTTP concerns and testable with a fake `LLMService`.
- **Tables:** `experiments(id UUID, name, dataset_id FK RESTRICT, status, prompt_template text, config JSONB, evaluator_config JSONB, cancel_requested bool, created_at, started_at, finished_at)`; `experiment_results(id UUID, experiment_id FK CASCADE, test_case_id FK RESTRICT, case_position int, provider, model, status, rendered_prompt text, attempts int, response_text text null, finish_reason, provider_request_id, reported_model, input_tokens, output_tokens, total_tokens, latency_ms float, effective_max_tokens, error_kind, error_message, started_at, finished_at)` with `UNIQUE(experiment_id, test_case_id, provider, model)` and an index on `(experiment_id, status)`. `config` holds `targets`, `system_prompt`, `temperature`, `max_tokens`, `timeout_seconds`, `max_attempts`. Enums are lowercase strings stored as varchar (no PG enum types, so adding a status needs no type migration).
- **Frozen inputs:** creation inserts all pending result rows in the creation transaction with `rendered_prompt` already rendered. This freezes the case set and exact prompt (answering the dataset-management reproducibility caveat) and makes execution independent of later dataset edits. Large experiments are inserted in batches; the case count times target count is capped at 10 000 results per experiment (422 otherwise).
- **Statuses and transitions:**
  - Experiment: `pending -> running -> completed | partially_failed | failed`; `pending|running -> cancelled`; `partially_failed|failed -> running` (retry-failed). `completed` and `cancelled` are final.
  - Result: `pending -> running -> succeeded | failed`; `pending -> cancelled`; `failed -> pending` (retry-failed); a stale `running -> running` (lease reclaim).
- **Idempotent claim:** the runner claims a result with one atomic `UPDATE ... SET status='running', attempts=attempts+1, started_at=now() WHERE id=:id AND (status='pending' OR (status='running' AND started_at < now() - lease)) RETURNING ...`. Zero rows means someone else owns it or it is finished, so the task returns without doing anything. The claim is committed before the model call; the call runs with no open transaction; the outcome is written with `WHERE id=:id AND status='running'` so a reclaimed stale worker cannot overwrite a newer outcome. The claim returns its `started_at`, and the outcome write also requires `started_at` to equal it, so a stale worker's late write is dropped even after a newer claim. The attempt budget is counted per run (`max_attempts` requests per claim) while the stored `attempts` is the cumulative number of model requests. Lease is 1800 seconds (a constant): `timeout_seconds` is capped at 120, so max_attempts (5) times timeout plus backoff stays far below it. Time spent waiting for the per-provider semaphore counts against the lease, which is why it has headroom.
- **Retry loop inside the task:** the attempt counter increments on every model request (the first through the claim, later ones in the loop). Retryable `LLMProviderError` leads to `sleep(min(30, base * 2**n))`, using `retry_after_seconds` if present (capped at 30) plus small jitter, and the loop stops at `max_attempts` (default 3, 1 to 5). Non-retryable kinds fail immediately. An unexpected exception records error kind `internal` with only the exception class name and is not retried. Rejected alternatives: Taskiq-level retries (no per-case attempt record) and delayed re-enqueue (needs scheduler/delay support not present in the broker setup). Trade-off: a backing-off task occupies a worker slot, bounded by the 30 second cap.
- **Concurrency:** a process-local `asyncio.Semaphore` per provider, size `LLM_MAX_CONCURRENCY` (default 4), wraps each model request but not the backoff sleep. Worker-wide task parallelism is still set by Taskiq worker options (documented in README). Limits are per worker process; global limits across workers are out of scope.
- **Finalization:** after persisting a result, the runner locks the experiment row (`FOR UPDATE`), counts non-terminal results and, when zero, sets the final status per the spec and `finished_at`. Lock ordering is experiment first in every writer (finalize, cancel, retry-failed, start), which avoids deadlocks. A finalization missed by a crash is repaired by the next redelivery or by calling start/retry again (retry-failed also finalizes, so an empty reset cannot leave the experiment running); reads never compute status on the fly, so the stored status stays authoritative.
- **Start / lost messages:** start moves `pending -> running`, commits, and then enqueues. If enqueueing fails midway, results remain pending and a second start enqueues them again. A second start also enqueues `running` results whose lease expired (crashed worker), so they are recovered without waiting for a broker redelivery; the response lists how many tasks were enqueued. Tasks carry only `result_id`.
- **Cancel:** sets `cancel_requested`, marks pending results `cancelled`, leaves running ones to finish. The finalizer sets `cancelled` when `cancel_requested` is true and nothing is running. A cancelled experiment is final (retry returns 409).
- **Prompt template:** plain text; only the literal tokens `{input}` and `{expected_output}` are replaced in a single regex pass over the template (sequential `str.replace` would re-scan an inserted case value, so one pass keeps braces and token-like text in cases inert). `{input}` is required. This avoids `str.format` attribute and index access on untrusted data. `{expected_output}` on a case without one renders an empty string.
- **Evaluator configuration:** accepted as a JSON object (max 10 KB serialized, no NUL) and stored verbatim; stage 4 defines its schema and execution. Until then it is data only.
- **Validation of providers:** creation checks `ProviderNotConfiguredError` conditions through `LLMService` readiness (key set and non-blank) so a missing key fails fast with 422 instead of producing a whole experiment of failed results. A key removed after creation yields failed results with error kind `configuration`, not retried.
- **Metrics:** only measured values are stored (tokens as reported, `latency_ms` measured by the adapter, `effective_max_tokens`); no totals are summed and no cost is computed.
- **API:** `POST /api/v1/experiments` (201), `GET /api/v1/experiments` (paginated), `GET /api/v1/experiments/{id}` (config + status counts), `GET /api/v1/experiments/{id}/results` (pagination, `status` filter), `POST .../start` (202), `POST .../cancel`, `POST .../retry-failed` (202). Invalid state transitions return 409, unknown ids 404, bad input 422. Domain errors (`ExperimentNotFoundError`, `InvalidTransitionError`) live in the service; the endpoint maps them.
- **Transactions and sessions:** API calls use the request session (service flushes, `get_session` commits). The runner opens its own short sessions from `get_session_factory()` for claim, persist and finalize.
- **Logging:** only ids, status, attempt, error kind and latency; never prompts, responses, keys or provider messages.
- **Tests:** unit tests (template rendering, status derivation, backoff computation); integration tests against PostgreSQL for creation, frozen case set, claim idempotency (including concurrent claims), lease reclaim, partial failure, retry-failed, cancel, finalization; runner tests with a fake `LLMService` (including retry sequences, retry-after cap and the concurrency limit); API tests; a Taskiq `InMemoryBroker` test that a started experiment is executed end to end and that enqueueing alone changes no result.

## Risks / Trade-offs

- [Backoff sleeps hold a worker slot] → capped at 30 seconds and bounded by max attempts.
- [Process-local concurrency limit] → total in-flight equals workers times limit; document it and tune per deployment; a shared limiter is a later change.
- [Large experiments create many rows and tasks] → 10 000 results cap, batched inserts, index on `(experiment_id, status)`.
- [Lost finalization after a crash] → the final status is only written by the last finisher; a redelivery, start or retry repairs it, and a pending/running count never lies because results are the source of truth.
- [Lease too short or too long] → generous 1800 seconds constant; a reclaim after a slow-but-alive worker is safe because the outcome write is conditional on `status='running'` (the slower worker's write is dropped, cost: one duplicated model call).
- [Frozen prompts duplicate stored text] → accepted for reproducibility; revisit with dataset versioning.

## Migration Plan

New Alembic revision `0003` creating both tables; downgrade drops them. No existing data is touched. Roll back by `alembic downgrade 0002`.

## Open Questions

- Whether a global (cross-worker) rate limiter is needed before real traffic; deferred until measured.
