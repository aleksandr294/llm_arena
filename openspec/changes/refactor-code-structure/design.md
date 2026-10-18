# Design

## Context

Findings from the audit done while planning (the apply phase re-verifies each with grep before moving anything):

**Dependencies built inside services (DI candidates)**
- Session-scoped services create their own repositories in `__init__`: `DatasetService` (datasets, cases), `ExperimentService` (datasets, cases, experiments, results), `UserService` (users), `AuthService` (users, organizations, tokens), `OrganizationService` (organizations, tokens), `MetricsService` (metrics repository).
- Services create other repositories or services ad hoc: `UserService` builds `OrganizationRepository` and `AuthService`; `MetricsService` and `export.prepare_export` build `ExperimentRepository`, `ExperimentResultRepository` and `MetricsService`; `EvaluationService` builds `ExperimentRepository` and `EvaluationRepository` per operation.
- `LLMService` owns a module-level registry of provider factories (`OpenAIProvider`, `AnthropicProvider`) and is itself constructed with `get_settings()` inside the experiments endpoint dependency and in `components/experiments/tasks.py`.
- Admin classes (`admin/users`, `admin/organizations`) build repositories and `OrganizationService` and call `get_session_factory()` directly; `cli.py` calls `get_session_factory()` directly.
- Already injected (keep): `ExperimentService(session, llm, enqueue)`, `ExperimentRunner(session_factory, llm_service, ...)`, the evaluation hook, the pricing file path.

**Exceptions** (23 classes) live in `service.py` files (datasets, experiments, users, organizations, auth, third_party/llm), in `auth/security.py` (`PasswordPolicyError`, `TokenError`, `InvalidTokenError`, `ExpiredTokenError`), in `metrics/pricing.py` (`PricingConfigError`), in `providers/base.py` (`LLMProviderError`) and in `core/access.py` (`OrganizationRequiredError`).

**Enums** (9) live in `core/access.py` (`Role`), `experiments/models.py` (`ExperimentStatus`, `ResultStatus`), `evaluation/schemas.py` (`OutcomeStatus`), `third_party/llm/schemas.py` (`ProviderName`, `LLMErrorKind`), `auth/security.py` (`TokenType`), `auth/service.py` (`RejectionReason`) and `api/v1/endpoints/metrics.py` (`ExportFormat`).

**Helpers**: `evaluation/schemas.py` imports the underscore-private `_reject_nul` from `datasets/schemas.py`; experiments and users reuse NUL checks through other private imports; email normalization, CSV neutralization and similar functions sit in the modules that first needed them.

**Tests**: nine `conftest.py` files (root, `tests/`, and one per area) with `engine` defined four times, `session_factory` four times, `client` and `owner` re-declared in six test modules, plus `world` fixtures in three modules.

**README**: 764 lines mixing setup with the API reference of every feature.

See the proposal for motivation.

## Goals / Non-Goals

**Goals:**
- Collaborators are visible in constructor signatures and replaceable in tests.
- Every kind of definition has one predictable home, so a reader knows where to look.
- One conftest, no duplicated fixtures.
- A README that onboards a new developer in minutes, with the reference material still available.

**Non-Goals:**
- Behavior changes, new features, new dependencies, DI frameworks (see proposal).

## Decisions

- **DI rule.** Constructor injection. A class receives what it collaborates with: other services, providers, clients, settings values, ports such as `enqueue`, and, for session-scoped services, the repositories bound to the request session. The wiring is done in composition roots only: FastAPI dependency functions (`api/v1/utils.py` and the endpoint modules), Taskiq task modules (`tasks.py`), `cli.py`, and the admin package. Classes that own the lifetime of short sessions (`EvaluationService`, `ExperimentRunner`, `prepare_export`, admin classes) take the `session_factory` and may construct repositories inside the unit of work they open, because a repository is only a thin wrapper over that session; their other collaborators (LLM service, other services, settings) are injected. No container library: FastAPI `Depends` (for HTTP handlers only; tasks, the CLI, the admin classes and the runner cannot use it) and plain factory functions are enough. Injection is by constructor, never by method argument. Collaborators owned by the caller's session or transaction (repositories, services that share the session) are required parameters with no hidden default. A stateless production default is allowed for infrastructure collaborators (for example the provider factories of `LLMService`) as an optional constructor parameter resolved with `if x is None` (never `x or Default()`, which misbehaves for falsy objects), so existing call sites keep working and tests can pass fakes.
- **Providers.** `LLMService` accepts an optional mapping of provider factories (default: the OpenAI and Anthropic adapters) instead of reading a module global, so tests and future providers do not patch module state. Provider-specific behavior stays inside adapters.
- **Where the wiring functions live.** Shared factories (`get_auth_service`, `get_settings_dependency`, ...) stay in `api/v1/utils.py`; component-specific ones stay next to their endpoints. Each composition root builds the same graph the code builds today, which keeps behavior identical.
- **Module layout per component.** `models.py`, `schemas.py`, `repository.py`, `service.py` as today, plus `exceptions.py`, `enums.py` and `utils.py` where the component has any such definitions (no empty files). Cross-component definitions live in `components/core/{exceptions,enums,utils,schemas}.py`; `third_party/llm/` gets its own `exceptions.py` and `enums.py`.
- **Move map.**
  - core: `Role` -> `core/enums.py`; `OrganizationRequiredError` -> `core/exceptions.py`; `Principal` -> `core/schemas.py` (`scope_clause` and `require_organization` stay in `core/access.py`); shared NUL-rejection helpers -> `core/utils.py`, replacing the private cross-component import.
  - datasets: `DatasetNotFoundError` -> `exceptions.py`.
  - experiments: `ExperimentStatus`, `ResultStatus` -> `enums.py`; `ExperimentNotFoundError`, `InvalidTransitionError`, `ExperimentValidationError` -> `exceptions.py`; pure helpers from `logic.py` -> `utils.py`.
  - evaluation: `OutcomeStatus` -> `enums.py`; `EvaluationInput` dataclass -> `schemas.py`.
  - users: `InvalidEmailError`, `DuplicateEmailError`, `UserForbiddenError`, `UserNotFoundError`, `UserConflictError`, `UserInputError` -> `exceptions.py`; `normalize_email` -> `utils.py`.
  - organizations: `OrganizationNotFoundError`, `DuplicateOrganizationNameError` -> `exceptions.py`.
  - auth: `InvalidCredentialsError`, `InvalidRefreshTokenError`, `AccessTokenRejectedError`, `PasswordPolicyError`, `TokenError`, `InvalidTokenError`, `ExpiredTokenError` -> `exceptions.py`; `TokenType`, `RejectionReason` -> `enums.py`.
  - metrics: `PricingConfigError` -> `exceptions.py`; `ExportFormat` -> `enums.py`; CSV neutralization and other reusable pure helpers -> `utils.py`.
  - third_party/llm: `UnsupportedProviderError`, `ProviderNotConfiguredError`, `LLMProviderError` -> `exceptions.py`; `ProviderName`, `LLMErrorKind` -> `enums.py`.
  - admin: `SessionUser` and other public data classes -> `admin/schemas.py` if more than one module uses them.
  Private single-use helpers and private data classes stay where they are; the criterion for `utils.py` is "used by more than one module or imported across components".
- **Leaf modules.** `exceptions.py` and `enums.py` import nothing from services, repositories or models (only the standard library, other leaf modules and `core`), so they cannot create import cycles; `models.py` imports its enums from `enums.py`.
- **No compatibility shims.** Names are moved and every import updated (including tests and the admin package); old locations do not re-export. Alembic revisions are hand-written SQL-level code and import no moved names (verified by import check).
- **Tests.** Exactly one `conftest.py`, at the repository root, containing every fixture used by more than one test module: a single `engine` fixture that creates the complete schema (all models imported once), `session_factory`, the app and anonymous client, `actors`/`World`-style helpers, and the panel fixtures shared by admin tests. All other `conftest.py` files are removed; fixtures used by only one module (`client` and `owner` variants, module worlds) live in that module. Plain helper modules (`tests/access.py`, `tests/*/helpers.py`, panel process helpers) are not fixtures and stay as modules. The complete-schema engine creates more tables per test than the narrow ones did; the suite time is measured before and after, and if it grows noticeably the engine fixture moves to a cheaper strategy (per-session schema creation with truncation) under the same name.
- **README and docs.** README sections: Summary, Technical Stack (versions read from `pyproject.toml`, `poetry.lock` and the Dockerfile; docker compose or podman compose), How to start locally, Commands, Architecture (layers, request and task flow, how it scales: stateless API processes, horizontally scaled workers with per-process concurrency limits, PostgreSQL locks and leases for safe duplicate delivery, RabbitMQ), Development methodology (OpenSpec flow, branches, conventional commits, Kan, agents, review, testing policy). The existing per-feature reference (datasets, experiments, evaluation, metrics, authentication, access control, admin panel, API documentation, migrations, quality checks, real-provider tests) moves unchanged to `docs/` pages linked from the README; commands in the new README are run once to verify them.
- **Safety net.** Behavior preservation is checked, not assumed: the generated OpenAPI schema, the settings fields, Taskiq task names and the Alembic head are compared before and after; the full test suite must keep passing with the same set of test cases (only fixture locations change); `python -c "import <module>"` succeeds for every module in a fresh interpreter (cycle check).

## Risks / Trade-offs

- [Large diff touching most modules] → do it in small, independently green batches (core, then groups of components, then DI, then tests, then docs) and commit per batch; behavior invariants checked after each.
- [Import cycles from moved definitions] → leaf-module rule, fresh-interpreter import check of every module.
- [Complete-schema engine fixture may slow the suite] → measure; fall back to a cheaper strategy under the same fixture name.
- [Moving API docs out of the README can break links or lose content] → move verbatim, check headings and code blocks are preserved, link from the README.
- [DI rule is a judgment call for session-owning services] → documented in README (Architecture) and CLAUDE.md so reviewers can apply it consistently.
- [Stacked on an unmerged branch] → rebase on `main` after the admin branch merges.

## Open Questions

- Whether to also add a `dependencies.py` per component for FastAPI wiring later; not needed for this change.
