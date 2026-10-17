# Tasks

## 1. Contract, evaluators and persistence

- [x] 1.1 Add `components/evaluation/schemas.py` (outcome status enum, `EvaluationOutcome`, per-type params models with `extra="forbid"`, the discriminated-union evaluator entry, `EvaluatorConfigSchema` with unique ids and at most 20 entries, a parse helper that normalizes with defaults) and verify unit tests for every valid and invalid configuration case, including error field paths and legacy `{}`
- [x] 1.2 Add `components/evaluation/evaluators.py` (`BaseEvaluator`, registry, `exact_match`, `contains`, `not_contains`, `length_between`, `json_valid`, `json_fields`, each with a `version`) and verify table-driven unit tests for every spec scenario plus determinism, skipped on missing expected output, deep JSON nesting, NUL bytes, bool-vs-integer typing and details never containing response text
- [x] 1.3 Add `components/evaluation/models.py` (`EvaluationResult`, unique `(experiment_result_id, evaluator_id)`, index `(experiment_id, evaluator_id, status)`), register it in `migrations/env.py`, generate and hand-review revision `0004`, add `EvaluationRepository` (insert-ignore, paginated list with filters, per-evaluator outcome counts, succeeded results missing evaluations) and verify `alembic upgrade head` and `downgrade 0003` succeed and integration tests for idempotent insert, filters, counts, missing-evaluation query and cascade delete

## 2. Service and triggering

- [x] 2.1 Add `EvaluationService` and `components/evaluation/tasks.py` (`evaluation.evaluate_result` taking only `result_id`, backfill enqueue, summary), and verify integration tests for two evaluators storing two rows, failed generation producing none, duplicate delivery keeping one row each, an evaluator exception stored as `error`, a legacy unparsable config meaning no evaluation, and the response and generation status staying unchanged
- [x] 2.2 Validate `evaluator_config` with the new parser in experiment creation (store the normalized dump), add the optional post-generation hook to `ExperimentRunner` (called only after a committed `succeeded` outcome, failures swallowed and logged) and wire the enqueue in `components/experiments/tasks.py`, and verify tests for the 422 field path, defaults frozen in the stored config, hook called once for success and never for failure, a failing hook not changing the result, and an `InMemoryBroker` test that a started experiment ends with stored evaluations

## 3. API

- [x] 3.1 Add `api/v1/endpoints/evaluations.py` (backfill `POST .../evaluate` 202, `GET .../evaluations`, `GET .../evaluations/summary`), register the router, and verify API tests for 202 with counts including zero, 404, 422 (bad pagination, bad status filter), filters, summary counts per outcome and `unevaluated_results`
- [x] 3.2 Verify with a test that response text and expected output sentinels never appear in captured logs or stored evaluation details, including a hostile response, on every evaluator type

## 4. Documentation and final checks

- [x] 4.1 Document the evaluator configuration, each evaluator's parameters and outcomes, the endpoints with `curl` examples and the excluded features in README.md, and verify the documented commands work against a running API with a scratch database
- [x] 4.2 Run `pytest` (including integration tests), `ruff check .`, `black --check .` and `mypy` and verify all pass, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-5 (`dehdes4oujgp`) to In-progress when work starts and to Review once the reviewer agent has reviewed it (see the `kan-review` skill).
- Work on branch `feat/deterministic-evaluation` (stacked on `feat/experiment-execution`), commit with the `git-conventional-commits` skill, do not push without being asked, and archive the change after approval.
