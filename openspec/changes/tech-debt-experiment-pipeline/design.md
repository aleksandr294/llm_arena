# Design

## Context

`ExperimentRunner.run` claims a result (`_claim`, lease = `started_at`,
1800 s), then `_execute` takes the per-provider semaphore around each model
call, then `_persist_safely` writes the outcome conditionally on the claim's
`started_at`. Export streams keyset batches ordered by
`(case_position, provider, model, id)`; `experiment_results` only has
`(experiment_id, status)` and the unique target constraint. Evaluation reads
a result through `EvaluationRepository.get_input`, which does not select
`response_truncated`. See proposal.md for motivation.

Branch `feat/tech-debt-experiment-pipeline`, worktree
`../llm-arena-tech-debt-pipeline`. Merge after `tech-debt-auth-hardening`
(migration `0008`); this change adds `0009`.

## Goals / Non-Goals

**Goals:**
- A result is never left `running`/`pending` without an explanation, and the
  lease measures only real work.
- Truncation is visible to evaluation and API clients.
- Export cost is linear in rows.

**Non-Goals:**
- No lease heartbeat/renewal; no change of the 1800 s lease.
- No changes to evaluators' verdicts or versions (details only).
- No new export format or trailer; failures abort the response.

## Decisions

1. **Slot before claim.** `run()` peeks the provider with a read-only
   `ExperimentResultRepository.get_provider(result_id)` (short scope, no
   lock), holds `self._semaphore(provider)` for the whole execution including
   retry backoff, then claims. Holding the slot while sleeping applies
   backpressure exactly when the provider is rate limiting. Alternative:
   renewing the lease while waiting, rejected as more machinery for the same
   effect. If the peek finds no row or a finished status, the delivery is
   skipped with the reason logged.
2. **Cancellation.** `run()` wraps the post-claim part in `try/except
   asyncio.CancelledError`; the handler persists a `FAILED` outcome (`kind
   interrupted`, message "Worker stopped before the request finished") through
   `asyncio.shield`, then re-raises. The write is conditional on
   `claimed_started_at`, so it cannot overwrite a newer claim.
3. **Fail unusable claims.** `_claim` returns a plain snapshot (provider
   string, model, config dict) and commits; `ProviderName(...)` and
   `ExperimentConfigSchema.model_validate` run outside the scope in a
   `try/except (ValueError, ValidationError)` that persists a `configuration`
   failure with class-name-only message.
4. **Retry budget:** `made` starts at `work.attempts` (the claim already
   counted this run's first request), so reclaims share the budget; a result
   already at or over `max_attempts` makes one request and no retries.
5. **Backoff:** `RETRY_AFTER_CAP_SECONDS = 120`. `backoff_delay` returns
   `min(cap, delay) + jitter` where the cap applies to the base delay only, so
   jitter always applies; a provider delay above the cap makes the runner
   return a `rate_limit` failure immediately (no sleep).
6. **Text safety.** `_clean`/`_bound_response` also replace `[\ud800-\udfff]`
   with `U+FFFD`. `_persist_safely` catches `(SQLAlchemyError, UnicodeError)`
   for the primary write; the fallback write is wrapped: on failure it logs
   the id with the class name and re-raises so the Taskiq task fails and the
   lease path takes over.
7. **Skip reasons.** `claim()` returning `None` is followed by a read of the
   status (same scope) and a log line `result %s skipped (finished|owned)`.
8. **One rollback helper.** `components/core/database.py` gains
   `rollback(session)` (drops pending `on_commit` callbacks like
   `session_scope` does, then rolls back); runner and
   `EvaluationService` use it. This change owns only the *addition* and these
   two call sites; the `core-api-cleanups` change edits other parts of the
   same file.
9. **Truncation in evaluation.** `EvaluationInput` gains
   `response_truncated: bool`, selected in `get_input`; `evaluate_result`
   merges `{"response_truncated": True}` into `details` only when true.
   Alternative: skip the evaluation, rejected because a truncated response can
   still legitimately fail (or pass) a check and skipping hides it.
10. **`/cases`:** `CaseResultRow` and `CaseTargetResultSchema` gain
    `response_truncated: bool` (always selected, cheap).
11. **Export.** Index `ix_experiment_results_export_order` on
    `(experiment_id, case_position, provider, model, id)` (migration `0009`);
    verified with `EXPLAIN` in a test (no sort node for the keyset query).
    `json_stream`/`csv_stream` log and then `raise` instead of `return`.
    Rows become a small `NamedTuple` instead of positional `r[7]` indexing,
    the `defaultdict` is typed, the redundant `get_scoped` in
    `prepare_export` is dropped (the metrics service call is scoped).
    `neutralize` strips leading ASCII whitespace for the prefix check and
    treats `|` as a prefix. Docs state snapshot semantics and that prompts are
    included in JSON with `include_responses`.
12. **Coupling.** `backoff_delay` and its constants are used by both the
    runner and `EvaluationService`, so they move to `components/core/utils.py`
    (CLAUDE.md: shared helpers live in core; imports updated, no shim). That
    removes the `evaluation -> experiments.utils` import. The remaining
    `experiments/tasks.py -> evaluation.tasks` import is entry-point wiring of
    the post-success hook, which CLAUDE.md allows for Taskiq tasks, and
    `experiments.schemas -> evaluation.schemas` is a one-way config-schema
    dependency; both stay and are documented here as accepted.

## Risks / Trade-offs

- [Holding a slot while backing off lowers throughput during rate limits] ->
  intended; tunable with `LLM_MAX_CONCURRENCY`.
- [Cancellation write itself may be cancelled/fail] -> shielded; if it fails
  the existing lease reclaim still recovers the result.
- [Aborted export response shows as a client error] -> intended (honest
  signalling), documented.
- [Index build locks `experiment_results` writes] -> `CREATE INDEX` is run in
  the migration transaction; the table is write-heavy only while experiments
  run, so deploy between runs (stated in docs/migrations.md).

## Migration Plan

Revision `0009` (`down_revision` `0007` while developing alone, re-parented
to `0008` when the auth change has merged): `CREATE INDEX
ix_experiment_results_export_order`. Downgrade drops it. No data change.
Deploy order: migrate, restart API and worker.
