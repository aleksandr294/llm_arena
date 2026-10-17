# Tasks

## 1. Persistence and scope helper

- [x] 1.1 Add `scope_clause` to `components/core/access.py`, add `organization_id` and `owner_id` to the `Dataset` and `Experiment` models, generate and hand-review revision `0006` (default organization, backfill then NOT NULL, indexes), and verify unit tests for `scope_clause` per role, `alembic upgrade head` on a database with existing datasets and experiments keeping all rows assigned to the default organization with null owners, `alembic check` showing no drift, and `downgrade 0005` working

## 2. Scoping the resources

- [x] 2.1 Make datasets scoped: services and repositories take a `Principal`, creation records owner and organization (SuperAdmin gets 403), reads and writes use the scope clause, list gains `created_from`, `created_to`, `owner_id` filters with range validation, endpoints use `get_principal`, and verify updated and new tests for staff isolation, admin and superadmin scope, 404 for out-of-scope datasets and cases, filters, and the legacy dataset rule
- [x] 2.2 Make experiments scoped: creation requires a dataset visible to the caller and records owner and organization, list gains `status`, `created_from`, `created_to`, `owner_id` filters, and get/results/start/cancel/retry-failed use the scope clause (workers unchanged), with verified tests for the spec scenarios including 404 on another user's dataset or experiment and combined filters
- [x] 2.3 Scope evaluations, metrics and export endpoints through the parent experiment, and verify tests that summary, comparison, cases, export, evaluations list/summary and backfill return 404 for out-of-scope experiments and that no data is streamed for them

## 3. Cross-cutting verification

- [x] 3.1 Add a role by resource by endpoint isolation matrix test (staff A, staff B, admin same organization, admin other organization, superadmin) over every protected endpoint, and a test that tokens, passwords, hashes and resource content never appear in captured logs during denied and allowed requests

## 4. Documentation and final checks

- [x] 4.1 Document in README.md the visibility rules per role, the filters, the breaking change and the migration steps, and verify the documented commands work against a running API with a scratch database
- [x] 4.2 Run `pytest` (including integration tests), `ruff check .`, `black --check .` and `mypy` and verify all pass, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-9 (`rbytgx49jc2a`) to In-progress when work starts and to Review once the reviewer agent has reviewed it (see the `kan-review` skill).
- Work on branch `feat/resource-access-control` (stacked on `feat/user-authentication`), commit with the `git-conventional-commits` skill, do not push without being asked, and archive the change after approval.
