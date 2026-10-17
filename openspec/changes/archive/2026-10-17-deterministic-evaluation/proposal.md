# Proposal

## Why

Kan card LLM-5 (stage 4 of the MVP plan). Experiments now store model responses, but nothing judges them. The platform's goal is reliable evaluation, so responses need an extensible evaluator contract and a first set of deterministic checks whose outcomes are stored separately from the generated response and can be recomputed, explained and compared later (stage 5) without any LLM call.

## What Changes

- Add an evaluator contract: a named, versioned check that takes a response and the case's expected output and returns `passed`, `failed`, `skipped` or `error` with machine-readable details.
- Add v1 evaluators: `exact_match`, `contains`, `not_contains`, `length_between`, `json_valid` and `json_fields` (required fields and optional types). Regular expressions are deliberately excluded (untrusted patterns and inputs).
- Define the experiment's `evaluator_config` shape (a list of evaluator entries) and validate it when an experiment is created.
- Persist one evaluation result per (generation result, evaluator) in a separate table that records evaluator id, type and version, never the response text.
- Evaluate each successfully generated result in a separate, idempotent Taskiq task triggered after generation, plus an endpoint to backfill missing evaluations.
- Add endpoints to read evaluation results and a per-evaluator outcome summary for an experiment.
- Generation status of results and experiments is not affected by evaluation outcomes.

## Capabilities

### New Capabilities
- `deterministic-evaluation`: evaluator contract, the v1 deterministic evaluators, their configuration and stored outcomes.

### Modified Capabilities
- `experiment-execution`: evaluator configuration is now validated against the known evaluator types at creation (previously any JSON object).

## Impact

- New component `components/evaluation/` (models, schemas, evaluators, repository, service, tasks) and `api/v1/endpoints/evaluations.py`; router registered in `api/v1/router.py`.
- New table `evaluation_results`; new Alembic revision after `0003`; models registered in `migrations/env.py`.
- `components/experiments/` gains the evaluator-config validation call and an optional post-generation hook; existing experiments with `{}` stay valid and simply have no evaluations.
- No new dependencies. This change is built on top of the `experiment-execution` branch.

## Assumptions

Recorded in design.md and revisable before apply: the evaluator set and parameters, `skipped` semantics for a missing expected output, evaluation as a separate task, no scores or weights, and exclusion of regex and LLM judges.

## Out of Scope

- Aggregation, scoring, comparison, cost and export (stage 5).
- LLM-as-a-judge and any evaluator that calls a model (stage 6).
- Regular-expression and JSON Schema evaluators, custom user-supplied code.
- Updating or deleting evaluator configuration after an experiment is created.
