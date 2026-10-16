# Tasks

## 1. Persistence

- [x] 1.1 Add `components/datasets/models.py` with `Dataset` and `TestCase` (UUID keys, FK with cascade, `UNIQUE(dataset_id, position)`, nullable expected output and metadata), register it in `migrations/env.py`, and verify `import components.datasets.models` works and `Base.metadata` lists both tables
- [x] 1.2 Generate the Alembic revision after `0001`, review it by hand, and verify `alembic upgrade head` then `alembic downgrade 0001` succeed on a clean PostgreSQL database
- [x] 1.3 Add `DatasetRepository` and `TestCaseRepository` (paginated listing with total, next-position lookup, case count) and verify integration tests for ordering, pagination, cascade delete and the unique position constraint

## 2. Schemas and service

- [x] 2.1 Add Pydantic schemas for dataset and test case create/read and paginated lists with the validation rules in the spec (trim, length limits, NUL rejection, non-empty input, metadata object) and verify unit tests for every invalid case
- [x] 2.2 Add `DatasetService` (create, list, get with case count, add case with row lock and next position, list cases) and `DatasetNotFoundError`, and verify integration tests for positions 1 and 2, null expected output, unknown dataset, and exact content round-trip including quotes, newlines and non-ASCII text
- [x] 2.3 Verify concurrent case creation with a test that adds cases to one dataset in parallel and asserts unique, gap-free positions

## 3. API

- [x] 3.1 Add `api/v1/endpoints/datasets.py` (`POST/GET /api/v1/datasets`, `GET /api/v1/datasets/{dataset_id}`, `POST/GET /api/v1/datasets/{dataset_id}/cases`), map not-found to 404, register the router, and verify API tests for 201, 404, 422 (blank name, empty input, bad metadata, malformed UUID, bad pagination), pagination totals and ordered case listing
- [x] 3.2 Verify the stored-content requirement with an API test that submits HTML and template syntax and reads it back unchanged, and that no content appears in captured logs

## 4. Documentation and final checks

- [x] 4.1 Document the endpoints with request examples in README.md and verify the documented `curl` commands work against a running instance
- [ ] 4.2 Run `poetry run pytest`, `ruff check .`, `black --check .` and `mypy` and verify all pass, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-2 to In-progress when work starts and to Review when the pull request opens.
- Request independent review, open a pull request from a dedicated branch, and archive the change after approval.
