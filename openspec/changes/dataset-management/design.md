# Design

## Context

Existing foundation: `get_session` (commit on success, rollback on error), `Base`, a generic `BaseRepository` (get/list/add/delete, transaction owned by the caller), `api/v1/router.py` with only health, and an empty baseline migration `0001`. `migrations/env.py` expects each component's models module to be imported there. There is no auth, so this change adds none. See proposal.md for motivation and the spec for behavior.

## Goals / Non-Goals

**Goals:**
- Follow the established layering: schemas, repository, service, thin endpoint.
- Keep test case order stable and numbering race-free.
- Keep the schema ready for later versioning and tenancy without implementing either.

**Non-Goals:**
- Versioning, import, update/delete, auth, tenancy, evaluation rules.

## Decisions

- **Component layout:** `components/datasets/{models,schemas,repository,service}.py` and `api/v1/endpoints/datasets.py`; no `tasks.py`. Tests in `tests/datasets/`.
- **Tables:** `datasets(id UUID pk, name varchar(255), description text null, created_at timestamptz)` and `test_cases(id UUID pk, dataset_id FK ON DELETE CASCADE, position int, input text, expected_output text null, metadata jsonb null, created_at)` with `UNIQUE(dataset_id, position)` and an index on `dataset_id`. UUID keys avoid enumerable ids and merge cleanly with later versioned or tenant-scoped data. The metadata column is named `metadata_` in Python because `metadata` is reserved on declarative models.
- **Dataset names are not unique** in this increment. The card does not ask for it, and a later tenancy change would have to rescope uniqueness anyway. Duplicate names are distinguishable by id.
- **Test case format** (open question in the technical specification): `input` and `expected_output` are plain text and `metadata` is an optional JSON object. Text is the most general form for prompts; structured inputs can be added in a later change without breaking this one.
- **Position allocation:** the service locks the dataset row (`SELECT ... FOR UPDATE`), reads `max(position)`, inserts `max + 1`. This serializes concurrent adds to one dataset and, together with the unique constraint, prevents duplicates. Alternative: retry on unique violation; rejected as noisier.
- **Append-only cases:** no update or delete endpoints. Adding cases to a dataset after an experiment ran changes what "the dataset" means, so reproducibility for experiments is only guaranteed once versioning exists; the experiment-execution change must record what it ran (for example case ids) or wait for versioning.
- **Validation** lives in Pydantic schemas (trim name, length limits, NUL rejection, non-empty input, metadata as object). Errors use FastAPI's default 422 body, which names the field. Missing datasets raise a domain `DatasetNotFoundError` that the endpoint maps to 404 `{"detail": "Dataset not found"}`; the service does not import FastAPI.
- **Pagination:** `limit`/`offset` query parameters, response `{items, total, limit, offset}`; ordered by `(created_at, id)` for datasets and `position` for cases so pages are stable. `case_count` is computed with a count query on the detail endpoint only.
- **Transactions:** the service uses the request session (flush, no commit); `get_session` commits after the handler, as the existing convention. Endpoints build the service via a dependency from `get_session`.
- **Logging:** none of the content fields are logged.

## Risks / Trade-offs

- [Cases can be appended after experiments exist] → documented limitation; versioning or run-time case snapshots are required before experiments claim reproducibility.
- [Large inputs inflate responses and storage] → 50000-character limits per field and a page size capped at 100.
- [Duplicate dataset names] → accepted for now; revisit with tenancy.
- [Text-only inputs may not fit structured evaluators] → `metadata` is available now; a typed input can be added later.
