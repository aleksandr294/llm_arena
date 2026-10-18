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

- **DI rule.** Constructor injection of the non-database collaborators: other services, providers, clients, settings values and ports such as `enqueue`. The database session is passed from the entry point (see below). Wiring happens at the entry points only: FastAPI dependency functions (`Depends`, HTTP only), Taskiq task functions, `cli.py` commands and the admin classes. Services never create sessions, session factories, settings or other services themselves. No container library. Injection is by constructor, never by method argument, except the session in the one-shot service methods of the task flows described below. A stateless production default is allowed for infrastructure collaborators (for example the provider factories of `LLMService`) as an optional constructor parameter resolved with `if x is None` (never `x or Default()`, which misbehaves for falsy objects), so existing call sites keep working and tests can pass fakes.
- **The session is the unit of work.** One business operation is one transaction, and the SQLAlchemy `AsyncSession` already is the unit of work, so no wrapper class is added. The session is obtained once at the entry point through dependency injection and passed down: HTTP endpoint -> service -> repository, and Taskiq task -> service -> repository (likewise CLI command and admin handler). Services and repositories never open or close sessions and never read session factories.
  - **Entry points.** HTTP handlers keep `Depends(get_session)`, now declared with `scope="function"` (one shared alias used by every endpoint), so the dependency's commit or rollback runs before the response is sent. This is a deliberate small fix to behavior that was verified on FastAPI 0.143: with the default dependency scope the teardown (our commit) runs after the response has been sent, so a commit error was invisible to the client and a success response could precede durable data. After the change a commit error becomes a 500 and the client sees success only for committed data. Tasks, `cli.py` and the admin handlers use the same semantics through one shared async context manager, `session_scope()` in `components/core/database.py` (commit once on success, roll back on error, always close); `get_session` is rebuilt on top of it so behavior is identical.
  - **Exactly one commit per operation.** Services do not commit in the middle of an operation. Side effects that must follow durable data are registered with a small helper `on_commit(session, callback)` (SQLAlchemy `after_commit` event, callbacks run in registration order after the successful commit). `ExperimentService.start`/`retry_failed` register the queue publishing this way and finish their operation with `await commit(session)` (the single commit of that operation, a documented explicit commit point) so the callbacks run inside the handler before the `202` response is built, exactly as today; the order (commit, then enqueue) and the recovery path (a failed enqueue leaves pending results that a repeated start re-enqueues, and the client sees the error) stay as today. Services never call `session.commit()` directly; they use `commit(session)` so callbacks are never skipped.
  - **Persist-then-reject.** The one documented case where an operation must keep its writes and still fail is refresh-token reuse: `AuthService` revokes the family, commits (the operation's single commit) and then raises, so the 401 is raised after the commit.
  - **A slow external call splits the operation.** A transaction must not stay open during a provider call. `ExperimentRunner` therefore performs three atomic operations per result (claim; model call with no database access; persist the outcome and finalize, with today's lock order). It receives a `session_scope` callable from the task, the only kind of service that does, and passes each short-lived session to its own steps. Reads feeding a pure computation (`EvaluationService`) stay in one session.
  - **Streaming.** A streaming response outlives the request-scoped dependency, so the endpoint passes `session_scope` and the export iterator opens one short read-only session per batch, as today.
  - **Repositories.** A service builds its repositories from the session it was given (`DatasetRepository(session)` and so on), which is the session passing through the layers; repositories are not injected one by one.
  - **Exactly one commit per operation.** The HTTP dependency yields the UoW and commits once after a successful handler (rolling back on error), exactly like `get_session` does today; a task or CLI command wraps one operation the same way. Services do not commit in the middle of an operation.
  - **Side effects after commit.** Publishing to the queue must happen only after the data is durable. `ExperimentService.start` and `retry_failed` change statuses and register the enqueue with `uow.after_commit(...)`; the UoW commits once and then runs the callbacks in order. The observable order (commit, then enqueue) and the recovery path (a failed enqueue leaves pending results that a repeated start re-enqueues) are unchanged; they no longer need a second commit.
  - **Persist-then-reject.** The single documented case where an operation must keep its writes and still fail is refresh-token reuse: `AuthService` revokes the family, commits once and raises afterwards, so the 401 is raised from a UoW that has already committed.
  - **Operations that include a slow external call are several operations.** A transaction must not stay open during a provider call. `ExperimentRunner` therefore runs three business operations per result, each atomic in its own UoW: claim, model call (no database access), and persist-outcome-plus-finalize (same experiment lock order as today). It receives a UoW factory from the task (the only kind of service that does), never a session. Reads that feed a pure computation (`EvaluationService`) stay in one UoW.
  - **Streaming.** A streaming response outlives the request-scoped dependency, so the endpoint passes a UoW factory and the export iterator opens one short read-only UoW per batch, as today.
  Fakes for tests subclass the domain UoW and replace its repositories and hook execution.
- **Providers.** `LLMService` accepts an optional mapping of provider factories (default: the OpenAI and Anthropic adapters) instead of reading a module global, so tests and future providers do not patch module state. Provider-specific behavior stays inside adapters.
- **Where the wiring functions live.** Each component with injected collaborators has a small `dependencies.py` of plain factory functions (`get_<name>_service(uow, ...)`, no FastAPI imports) that is the single place where that service is wired. HTTP `Depends` functions (in `api/v1/utils.py` and next to the endpoints), tasks, `cli.py`, admin classes and tests call these factories; HTTP dependencies that yield the UoW (`get_<name>_uow`) keep the existing override points used by the tests.
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
- [Splitting the runner into three operations, and after-commit callbacks that can fail] -> both mirror today's behavior (separate claim/persist transactions; a failed enqueue leaves pending results recoverable by a repeated start) and are covered by tests that count commits and assert callback order and failure handling.
- [Stacked on an unmerged branch] → rebase on `main` after the admin branch merges.

## Open Questions

- Resolved during apply: the user clarified that the unit of work is the session passed from the entry point down through the layers, not a new class. A UoW wrapper was prototyped, found redundant and removed; see the session bullets.
