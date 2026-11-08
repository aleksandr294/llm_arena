# Tasks

Branch: `feat/tech-debt-experiment-pipeline` (worktree `../llm-arena-tech-debt-pipeline`).
Kan cards: `172m85cmpajg`, `q4saebmpzvc8`, `rqepqhnk0tdu`.

## 1. Core helper and runner ordering

- [ ] 1.1 Add `rollback(session)` to `components/core/database.py` (clears pending `on_commit` callbacks, then rolls back) and use it in `ExperimentRunner._claim` and `EvaluationService`; add a test that callbacks are dropped; verify `grep -rn "session.rollback()" components/experiments components/evaluation` returns nothing.
- [ ] 1.2 Add `ExperimentResultRepository.get_provider`; take the provider slot before the claim in `run()` and hold it through retries; log skip reasons (finished/owned); add runner tests with a one-slot semaphore proving waiting results are not claimed and the lease is untouched; verify duplicate delivery makes no provider call.
- [ ] 1.3 Make `_claim` return a plain snapshot and fail unusable provider/config with kind `configuration`; test unknown provider and invalid stored config; verify the result ends failed, not pending.
- [ ] 1.4 Handle `CancelledError` after the claim (shielded `interrupted` failure, then re-raise); test cancelling during a slow fake provider call; verify the experiment is finalized and `retry-failed` re-runs it.
- [ ] 1.5 Make the retry budget span reclaims (`made = work.attempts`); test a reclaimed result with 2 of 3 attempts; verify at most one more request.

## 2. Backoff and text safety

- [ ] 2.1 Move `backoff_delay` and its constants to `components/core/utils.py` (update the runner, `EvaluationService` and tests; no shim; verify `grep -rn "experiments.utils import.*backoff" .` is empty), add `RETRY_AFTER_CAP_SECONDS`, apply jitter after the cap and fail fast with `rate_limit` above the cap; update `backoff_delay` unit tests (60 s honored, 600 s fails, jitter present at the cap); verify the evaluation service's use of `backoff_delay` still passes its tests.
- [ ] 2.2 Scrub lone surrogates in `_clean`/`_bound_response`, widen the primary catch, protect the fallback write; fix the `original_chars` log to the cleaned length; add hostile-input tests from `tests/security/helpers.py` plus a surrogate case and a both-writes-fail case; verify the result is never left running.
- [ ] 2.3 Fix the stale comment at `components/experiments/models.py` (`response_truncated`); verify the worker still triggers evaluation after a succeeded result (existing `tests/evaluation/test_tasks.py` passes) and that `grep -rn "components.evaluation" components/experiments` lists only `tasks.py` and `schemas.py`.
- [ ] 2.4 Update `docs/experiments-api.md` (error kinds `interrupted`/`configuration`, retry semantics, lease note); verify the documented kinds match the code.

## 3. Truncation visibility

- [ ] 3.1 Add `response_truncated` to `EvaluationInput`/`get_input` and merge it into stored `details` when true; add evaluation service tests (json_valid on a truncated response keeps its verdict and gains the flag; complete response has no flag); verify `docs/evaluation-api.md` is updated.
- [ ] 3.2 Add `response_truncated` to `CaseResultRow`, `case_results` and `CaseTargetResultSchema`; add repository and API tests with and without `include_responses`; verify `docs/metrics-api.md` and the OpenAPI schema show the field.

## 4. Export

- [ ] 4.1 Add model index and migration `0009` with upgrade/downgrade; add `tests/migrations/test_0009_export_index.py`; verify an `EXPLAIN` test of the keyset query shows the index and no sort.
- [ ] 4.2 Re-raise mid-stream failures in `json_stream`/`csv_stream`; add tests that a failing batch aborts the response and logs no row content; verify the success path logs are unchanged.
- [ ] 4.3 Extend `neutralize` (leading whitespace, pipe); add parametrized tests with the hostile corpus; keep the unit test for existing prefixes green.
- [ ] 4.4 Replace positional row indexing with a `NamedTuple`, type the `defaultdict`, drop the redundant `get_scoped`; verify export tests pass unchanged and `mypy` is clean.
- [ ] 4.5 Document snapshot semantics, `finished` header flag, and that prompts appear in JSON with `include_responses` in `docs/metrics-api.md`; verify links resolve.

## 5. Integration

- [ ] 5.1 Run `pytest`, `ruff check`, `black --check`, `mypy`, Bandit and `openspec validate tech-debt-experiment-pipeline --strict`; report real results.

## Workflow follow-up

- Move the three Kan cards to `In-progress` at start and to `Review` after the reviewer agent.
- Re-parent `0009` onto `0008` once `tech-debt-auth-hardening` merged.
- Archive the change after review requirements are satisfied.
