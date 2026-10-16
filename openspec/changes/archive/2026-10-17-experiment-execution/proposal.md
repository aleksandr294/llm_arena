# Proposal

## Why

Kan card LLM-4 (stage 3 of the MVP plan). Datasets (stage 1) and the provider interface (stage 2) exist, but nothing runs a dataset against models. An experiment is the unit that turns them into comparable results, and it must be reliable: partial failures cannot lose finished results, a redelivered task cannot duplicate work, and failed cases must be retryable without rerunning successful ones.

## What Changes

- Add experiments: a named run of one dataset against 1 to 10 provider/model targets, with a prompt template, generation parameters and an evaluator configuration, frozen at creation.
- Create one pending result per test case and model at creation, so the case set is fixed even if the dataset grows later.
- Execute results asynchronously with Taskiq and RabbitMQ, one task per result, each persisted independently with status, response, measured metrics (null when unknown), error kind and attempt count.
- Bound concurrency per provider, apply a per-request timeout and a bounded retry with exponential backoff for retryable provider errors only.
- Make execution safe against duplicate delivery and worker crashes.
- Derive the experiment status (`completed`, `partially_failed`, `failed`, `cancelled`) from its results.
- Add endpoints to create, start, cancel, retry failed results, read an experiment and page its results.
- Add one setting for provider concurrency and a new Alembic revision.

## Capabilities

### New Capabilities
- `experiment-execution`: creating, running, cancelling, retrying and reading experiments and their per-case results.

### Modified Capabilities

## Impact

- New component `components/experiments/` (models, schemas, repository, service, runner, tasks) and `api/v1/endpoints/experiments.py`, registered in `api/v1/router.py`.
- New tables `experiments` and `experiment_results`; new Alembic revision after `0002`; models registered in `migrations/env.py`.
- `components/core/config.py` and `.env.example` gain `LLM_MAX_CONCURRENCY`.
- Reuses `LLMService` and `LLMProviderError` from `provider-abstraction`; this change is built on top of that branch.
- No new dependencies.

## Assumptions

Decided without a separate approval round because the card's open decisions have a clear default; each is recorded in design.md and can be revisited before apply: statuses and transitions, cancellation semantics, retry limits, idempotency by atomic claim, prompt template syntax, opaque evaluator configuration.

## Out of Scope

- Running evaluators and scoring (stage 4); the evaluator configuration is stored and validated as a JSON object only.
- Cost calculation and comparison/export (stage 5); only measured tokens and latency are stored.
- LLM-as-a-judge, authentication, multi-tenancy, dataset versioning.
- Streaming, tool calling and multi-turn prompts.
- Scheduling, priorities, and cross-worker global rate limiting.
