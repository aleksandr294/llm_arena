# Proposal

## Why

Kan card LLM-2 (stage 1 of the MVP plan). Experiments need a stable, named set of test cases to run the same inputs against several models, but the platform has no way to store or read such sets. Datasets are the first domain component and the input for every later stage (providers, experiment execution, evaluation).

## What Changes

- Add `Dataset` and `TestCase` persistence with an Alembic migration.
- Add an HTTP API to create a dataset, list datasets, and get a dataset by id.
- Add an HTTP API to add a test case to a dataset and to list a dataset's test cases.
- Validate input and return clear, field-level errors for incorrect requests.
- Add unit and integration tests for validation, persistence and the API.
- No new dependencies and no changes to existing endpoints.

## Capabilities

### New Capabilities
- `dataset-management`: creating, listing and reading datasets, and adding and reading their test cases, with input validation.

### Modified Capabilities

## Impact

- New component `components/datasets` (models, schemas, repository, service) and `api/v1/endpoints/datasets.py`, registered in `api/v1/router.py`.
- New tables `datasets` and `test_cases`; new Alembic revision after `0001`; the models module is registered in `migrations/env.py`.
- New tests under `tests/datasets/`.
- Existing session handling, `BaseRepository` and health endpoints are reused unchanged.

## Out of Scope

- CSV/JSON import and dataset versioning (explicitly deferred by the card).
- Updating or deleting datasets and test cases.
- Authentication, users, organizations and multi-tenancy.
- Evaluation criteria beyond storing an optional expected output.
