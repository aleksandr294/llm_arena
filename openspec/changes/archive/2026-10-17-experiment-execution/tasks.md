# Tasks

## 1. Persistence

- [x] 1.1 Add `components/experiments/models.py` (`Experiment`, `ExperimentResult`, status enums as lowercase strings, unique `(experiment_id, test_case_id, provider, model)`, index `(experiment_id, status)`), register it in `migrations/env.py`, and verify both tables appear in `Base.metadata`
- [x] 1.2 Generate and hand-review Alembic revision `0003`, and verify `alembic upgrade head` then `alembic downgrade 0002` succeed on a clean PostgreSQL database
- [x] 1.3 Add `ExperimentRepository` and `ExperimentResultRepository` (paginated listing with status filter, status counts, atomic claim, conditional outcome write, non-terminal count, row lock on experiment) and verify integration tests for ordering, pagination, counts, claim being granted to exactly one of several concurrent claimers, stale-lease reclaim, and cascade rules

## 2. Schemas and rendering

- [x] 2.1 Add Pydantic schemas for experiment create/read, config, targets, result read and paginated lists with the validation rules in the spec (template needs `{input}`, 1 to 10 distinct targets, max attempts 1 to 5, evaluator config object up to 10 KB, no NUL), and verify unit tests for every invalid case
- [x] 2.2 Add the prompt renderer (literal `{input}` / `{expected_output}` replacement, no re-scanning of case content) and the status-derivation and backoff helpers, and verify unit tests including braces and token-like text in case input, null expected output, and each final-status case

## 3. Service and runner

- [x] 3.1 Add `ExperimentService` (create with frozen results and provider-readiness check, start, cancel, retry-failed, get with counts, list, list results) with `ExperimentNotFoundError` and `InvalidTransitionError`, and verify integration tests for creation counts, frozen case set after the dataset grows, unknown dataset, 409 transitions, start-again enqueueing only pending results, and cancel semantics
- [x] 3.2 Add `ExperimentRunner` (claim, rendered prompt call through `LLMService`, retry loop with backoff and retry-after cap, per-provider semaphore, conditional persist of measured metrics, sanitized error, finalization under experiment lock) and verify tests with a fake `LLMService` for success, null usage, rate limit then success, authentication failure, exhausted attempts, unexpected exception, duplicate delivery making one request, stale-lease reclaim, partial failure, retry-failed rerunning only failed results, and the concurrency limit never exceeded
- [x] 3.3 Add `LLM_MAX_CONCURRENCY` to settings and `.env.example`, add `tasks.py` with the thin Taskiq task taking only `result_id`, and verify a `InMemoryBroker` test that a started experiment is executed to a final status, that enqueueing alone leaves results pending, and that `tests/test_config.py` passes

## 4. API

- [x] 4.1 Add `api/v1/endpoints/experiments.py` with the seven endpoints, map domain errors to 404/409, register the router, and verify API tests for 201, 202, 404, 409, 422 (empty targets, duplicate targets, template without `{input}`, unconfigured provider, bad evaluator config, bad pagination), status counts and the results filter
- [x] 4.2 Verify with a test that API keys, prompts, responses and provider error text do not appear in captured logs or stored error messages on success and failure paths

## 5. Documentation and final checks

- [x] 5.1 Document the endpoints with `curl` examples, the status model, cancellation and retry semantics, and the worker concurrency settings in README.md, and verify the documented commands work against a running API and worker using a mocked provider or a scratch database
- [x] 5.2 Run `pytest` (including integration tests), `ruff check .`, `black --check .` and `mypy` and verify all pass, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-4 (`pizwncj1w1nn`) to In-progress when work starts and to Review once the reviewer agent has reviewed it (see the `kan-review` skill).
- Work on branch `feat/experiment-execution` (stacked on `feat/provider-abstraction`), commit with the `git-conventional-commits` skill, do not push without being asked, and archive the change after approval.
