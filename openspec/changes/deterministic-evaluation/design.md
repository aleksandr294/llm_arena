# Design

## Context

Experiments (stage 3) store `response_text` per succeeded `ExperimentResult` and freeze an opaque `evaluator_config` object on the `Experiment`; test cases carry an optional `expected_output`. Generation runs in `ExperimentRunner` via a Taskiq task; its outcome write and finalization must not depend on anything evaluation does. CLAUDE.md requires separating generation from evaluation, deterministic evaluators first, and storing enough configuration to explain results. See proposal.md for motivation and the spec for behavior. This change is stacked on the `experiment-execution` branch.

## Goals / Non-Goals

**Goals:**
- A small, extensible evaluator contract where adding an evaluator means adding one parameter model, one class and one registry entry.
- Outcomes stored apart from responses, reproducible from stored identifiers (id, type, version) plus the frozen configuration.
- Idempotent, retry-safe evaluation that never blocks or alters generation.

**Non-Goals:**
- Scores, weights, aggregation, comparison, LLM judges, regex, JSON Schema (see proposal).

## Decisions

- **Layout:** `components/evaluation/{models,schemas,evaluators,repository,service,tasks}.py` and `api/v1/endpoints/evaluations.py`; tests in `tests/evaluation/`. The evaluation component depends on experiments' models for reading results; experiments depend only on `evaluation.schemas` (configuration parsing), so there is no import cycle.
- **Contract:** `BaseEvaluator` with class attributes `type: str` and `version: int`, a Pydantic params model, and `evaluate(response_text: str, expected_output: str | None) -> EvaluationOutcome`. `EvaluationOutcome(status, details)` where status is a lowercase `StrEnum` (`passed`, `failed`, `skipped`, `error`) and details is a small JSON object of reason codes and configured field paths only, never response text. A plain dict `EVALUATORS: dict[str, type[BaseEvaluator]]` is the registry; no plugin discovery, no dynamic import. Alternative rejected: free-form callables in config (code execution risk).
- **Configuration:** `{"evaluators": [{"id", "type", ...params}]}`, validated by a Pydantic discriminated union on `type` (`extra="forbid"` on every model), so errors carry field paths for the 422. Ids are unique within the list, at most 20 entries. The experiment stores the normalized dump with defaults filled in (so defaults are frozen with the experiment). A legacy stored config that no longer parses (stage-3 accepted any object) is treated as "no evaluators" with a warning log; no data migration.
- **v1 evaluators:**
  - `exact_match(trim=true, case_sensitive=true, collapse_whitespace=false)`: normalizes both sides (trim, collapse, casefold in that order; collapsing also removes leading and trailing whitespace) and compares; `skipped` when expected output is null.
  - `contains(values: 1..20 strings up to 1000 chars, mode: all|any, case_sensitive=true)` and `not_contains(values, case_sensitive=true)`.
  - `length_between(min_length?, max_length?)`, at least one bound, bounds >= 0, min <= max.
  - `json_valid(require_object=false)`: `json.loads` on the stripped response; `RecursionError`, `ValueError` and NUL-containing input count as `failed` with a reason code.
  - `json_fields(required: list of dot paths, types: map path -> string|number|integer|boolean|array|object|null)`: walks object keys only (no array indexing; keys containing a dot are unsupported and documented); `integer` and `number` exclude booleans; details list missing and mistyped paths.
  - Regex is excluded on purpose: patterns come from configuration and inputs from models, Python's `re` has no timeout (ReDoS), and a safe engine would be a new dependency.
- **Persistence:** table `evaluation_results(id UUID, experiment_result_id FK CASCADE, experiment_id FK CASCADE, evaluator_id varchar(64), evaluator_type varchar(32), evaluator_version int, status varchar(16), details JSONB, created_at)`, `UNIQUE(experiment_result_id, evaluator_id)` and an index on `(experiment_id, evaluator_id, status)`. `experiment_id` is denormalized so summaries need no join. Inserts use `ON CONFLICT DO NOTHING`, which makes redelivery and repeated backfill idempotent and leaves existing outcomes unchanged. Statuses are varchar, consistent with stage 3.
- **Separation from generation:** the runner gets an optional post-generation hook, called after the outcome transaction commits and only for a stored `succeeded` outcome. It enqueues `evaluation.evaluate_result(result_id)`. The hook's failure is caught and logged and never changes the generation result (a missed trigger is repaired by backfill). The evaluate task reads the result, its experiment's frozen config and the case's expected output in one short read, runs the pure evaluators with no open transaction, then inserts the outcomes. It ignores results that are not `succeeded`. An evaluator that raises is recorded as `error` with only the exception class name.
- **Backfill:** `POST /api/v1/experiments/{id}/evaluate` finds succeeded results with fewer stored evaluation rows than configured evaluators and enqueues the task for them (202, `{enqueued: N, remaining: M}`). At most 1000 results (in case order) are enqueued per request and `remaining` counts those still lacking evaluation after the batch, so large backlogs are processed in batches by calling again. The summary counts unevaluated results in the database without loading ids. It is allowed in any experiment status and is a no-op without evaluators. Evaluator configuration is frozen, so re-evaluation with changed rules is out of scope.
- **Reading:** `GET /api/v1/experiments/{id}/evaluations` (pagination, `evaluator_id` and `status` filters) and `GET /api/v1/experiments/{id}/evaluations/summary` returning per configured evaluator its type, version and outcome counts, plus `unevaluated_results` (succeeded results still lacking some evaluation), so asynchronous evaluation is visible rather than implied. Domain errors map to 404 as elsewhere.
- **Untrusted data and CPU:** evaluators never use `eval`, `exec`, `format` with untrusted templates or compiled patterns. Work is linear in response length (bounded by the model output limit); JSON depth is handled by catching `RecursionError`. Logs carry ids, evaluator ids and outcomes only.
- **Reproducibility:** the stored `(evaluator_id, type, version)` plus the experiment's frozen configuration and the stored response and expected output (case) explain every outcome; a behavior change of an evaluator must bump its `version`.

## Risks / Trade-offs

- [Cases are append-only but expected output is read live at evaluation time] → acceptable now because cases are immutable; if update support is added, snapshot the expected output on the generation result.
- [Evaluation lags generation] → the summary exposes `unevaluated_results`; backfill repairs lost triggers.
- [Stage-3 experiments with arbitrary evaluator_config] → treated as no evaluators; documented.
- [Large responses] → linear-time checks only; revisit with a response size cap if the provider limits grow.
- [Type and path semantics of json_fields are intentionally simple] → documented limits; JSON Schema can be added as a new evaluator type later without changing the contract.

## Migration Plan

New Alembic revision `0004` creating `evaluation_results`; downgrade drops it. No changes to existing rows. Roll back with `alembic downgrade 0003`.
