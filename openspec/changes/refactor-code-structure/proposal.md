# Proposal

## Why

Kan card LLM-17 ("Рефакторинг"). The codebase grew quickly through eight changes and the structure drifted from what the team wants: services build their own repositories and other services, exceptions, enums and shared helpers are scattered across `service.py`, `models.py`, `security.py` and endpoint modules (one component even imports an underscore-private helper from another), the test suite has nine `conftest.py` files with the same fixtures defined three to four times, and the 764-line README mixes onboarding with a long API reference. This change tidies all of that without changing any behavior.

## What Changes

- **Dependency injection and Unit of Work.** Audit every service, runner and admin class for dependencies on other services, repositories, providers and settings; inject collaborators through constructors and keep the wiring at the entry points (FastAPI dependencies, Taskiq task functions, `cli.py`, the admin package). Introduce a Unit of Work created at the entry point and passed endpoint -> service -> repository (task -> service -> repository), so services stop opening sessions and the transaction boundaries become explicit. Add fake-based unit tests that show the wiring works without a database.
- **Module layout.** Give every component the files shown in the card: `exceptions.py`, `enums.py` and `utils.py` alongside `models.py`, `schemas.py`, `repository.py`, `service.py`; shared ones go to `components/core/`. Move exceptions and enums out of service, model, security and endpoint modules, move reusable helpers to `utils.py`, and move public data classes into `schemas.py`.
- **Tests.** Keep exactly one `conftest.py` with all shared fixtures; fixtures used by a single test module live in that module.
- **Docs.** Rewrite `README.md` into the blocks Summary, Technical Stack, How to start locally, Commands, Architecture and Development methodology, moving the long per-feature API reference into `docs/` without losing content. Update the CLAUDE.md structure example and conventions.
- **No behavior change.** HTTP contracts, database schema, settings, task names, CLI and admin behavior stay identical; there are no migrations.

## Capabilities

### New Capabilities

### Modified Capabilities

This is a behavior-preserving refactor (`skip_specs: true` in the change metadata): no requirement changes.

## Impact

- Touches most Python modules (imports and constructor signatures), all `tests/` fixtures, `README.md`, new `docs/` pages, and `CLAUDE.md`.
- No new dependencies, no database changes, no configuration changes.
- Built on top of the `admin-panel-and-api-docs` branch because the refactor covers the `admin/` package, which `main` does not contain yet.

## Assumptions

Recorded in design.md and revisable before apply: dependency injection means constructor injection with wiring at the composition roots (no container library); repositories are injected into session-scoped services, while services that own short sessions per operation may still construct repositories inside their own unit of work; no compatibility re-exports after moving names; the API reference moves to `docs/` rather than being deleted; shared fixtures use one complete-schema engine fixture.

## Out of Scope

- Any change to behavior, API shapes, database schema, settings or error messages.
- New features, performance work, or parallel test execution (separate Kan card).
- A dependency-injection framework or container library.
- Renaming components or splitting the repository into packages.
