# CLAUDE.md

## Project Overview

LLM Arena for Developers is a backend platform for evaluating and comparing LLM providers and models.

The platform allows users to:
- Create and manage datasets of evaluation cases.
- Configure and execute experiments against multiple LLM models.
- Evaluate model responses using deterministic checks and configurable evaluators.
- Compare response quality, latency, token usage, cost, and error rates.
- Reproduce experiments using versioned datasets, prompts, and evaluation configurations.

The primary goal is reliable, reproducible evaluation rather than simply displaying responses from different LLMs.

## Tech Stack

- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- PostgreSQL
- Taskiq
- RabbitMQ
- OpenAI SDK
- Anthropic SDK
- Pytest
- Poetry
- Ruff, Black, mypy, pre-commit
- Docker, Docker Compose

Use the existing project dependencies and conventions. Do not introduce new frameworks or dependencies without a clear reason.

## Architecture

Keep responsibilities separated:

- **API layer:** HTTP endpoints, request validation, authentication, and response serialization.
- **Service layer:** business logic and orchestration of datasets, experiments, evaluations, and comparisons.
- **Repository layer:** persistence and database queries.
- **Provider adapters:** model-specific API calls and provider-specific error handling.
- **Evaluation layer:** deterministic checks and evaluator implementations.
- **Task layer:** asynchronous experiment execution and task lifecycle management.

Avoid coupling business logic to FastAPI handlers, database models, or provider SDKs.

## Project Structure

The backend uses a modular structure. Keep API handling, business logic, persistence, and external integrations separated.

### Example structure:

```
llm-arena/
├── api/
│   ├── app.py                   # create_app
│   └── v1/
│       ├── router.py
│       ├── utils.py             # shared Depends helpers
│       ├── docs.py
│       └── endpoints/
├── admin/                       # fastadmin panel, per-component admin classes
│   ├── core.py
│   ├── schemas.py
│   └── <component_name>/admin.py
├── components/
│   ├── core/                    # Base classes, config, DB, BaseRepository
│   │   ├── config.py
│   │   ├── database.py          # session_scope, commit, on_commit, get_session
│   │   ├── repository.py        # BaseRepository
│   │   ├── access.py
│   │   ├── enums.py             # shared enums
│   │   ├── exceptions.py        # shared exceptions
│   │   ├── schemas.py           # shared schemas / data classes
│   │   ├── utils.py             # shared helpers
│   │   └── health.py
│   ├── <component_name>/
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── repository.py
│   │   ├── service.py
│   │   ├── tasks.py
│   │   ├── exceptions.py        # where the component has any
│   │   ├── enums.py             # where the component has any
│   │   ├── utils.py             # where the component has any
│   │   └── dependencies.py      # build_* factories wiring the service
│   └── third_party/
│       └── llm/
│           ├── providers/       # BaseLLMProvider + Anthropic / OpenAI 
│           ├── schemas.py       
│           ├── exceptions.py
│           ├── enums.py
│           └── service.py       # LLMService
├── migrations/                  # Alembic
├── tests/                       # per component, mirrors components/
├── docs/                        # reference pages linked from README.md
├── main.py                      # python main.py API
├── cli.py                       # python cli.py create-superadmin
├── taskiq_app.py                     
├── conftest.py                  # the only conftest.py
├── .env.example
└── CLAUDE.md
```
This is the intended structure, not a requirement to create every file immediately. Follow the existing repository structure if it has already been established.

### Conventions

- **Exceptions, enums, data classes, helpers:** each has one home. `exceptions.py` and `enums.py` are leaf modules (they import only the standard library, other leaf modules and `core`, never services, repositories or models). Definitions used by several components live in `components/core/{exceptions,enums,schemas,utils}.py`; private single-use helpers stay where they are. Move a name and update every import; do not leave re-export shims.
- **Dependency injection:** pass non-database collaborators (other services, providers, clients, settings values, ports such as `enqueue`) through constructors. Wire them only at entry points (FastAPI `Depends`, Taskiq tasks, `cli.py`, admin classes) with the `build_*` factories in each component's `dependencies.py` (plain functions, no FastAPI imports). Services never create sessions, settings or other services. An optional production default is allowed only as a constructor parameter resolved with `if x is None` (never `x or Default()`). No DI library.
- **Session and transactions:** the entry point obtains the session and passes it endpoint/task -> service -> repository; services and repositories never open sessions. One business operation is one commit, through `session_scope()` (tasks, CLI, admin) or the HTTP `get_session` dependency (declared with `scope="function"`). Register side effects that must follow durable data with `on_commit`; where an operation must finish with a commit so they run, use `commit(session)`. Do not call `session.commit()` outside `components/core/database.py`. An operation with a slow external call is split into short operations instead of holding a transaction open (see `ExperimentRunner`).
- **Tests:** exactly one `conftest.py`, at the repository root, holding fixtures used by more than one test module. Fixtures used by a single module stay in that module; plain helper modules (`tests/**/helpers.py`) are not fixtures.

## Component Responsibilities

- **models.py:** SQLAlchemy database models.

- **schemas.py:** Pydantic request and response schemas.

- **repository.py:** Database queries and persistence operations.

- **service.py:** Business logic and transaction coordination.

- **tests/:** Unit and integration tests organized by component.

Do not create empty files or unnecessary abstraction layers. Add only the files required by the component.

## LLM Provider System

Provider-specific implementations must follow a shared provider interface.

Initial providers:
- Anthropic
- OpenAI

Additional providers, such as Ollama, may be added when required.

The provider interface should support the capabilities required by the platform, such as:
- Sending a model request.
- Returning a normalized response.
- Reporting token usage when available.
- Exposing provider request identifiers when available.
- Normalizing errors and timeouts.
- Supporting structured output and tool calling only where the provider and model support them.

Do not assume every provider exposes identical capabilities or metrics.

Keep provider-specific SDK behavior inside adapters. Do not spread provider-specific conditionals throughout the business logic.

API keys must be supplied through secure configuration and must never be committed or logged.

## Experiment Execution

An experiment evaluates a defined set of cases against selected models using a specific configuration.

- Persist experiment configuration and status.
- Persist individual case results independently.
- Support partial failure without losing successful results.
- Bound concurrency and handle provider rate limits.
- Apply explicit timeout and retry policies.
- Make task execution safe against duplicate delivery.
- Allow failed cases to be retried without rerunning successful cases unnecessarily.
- Track execution status and report failures explicitly.

Do not treat task submission as proof that execution succeeded.

## Evaluation and Reproducibility

- Separate model generation from response evaluation.
- Support deterministic evaluators before introducing LLM-as-a-judge.
- Version datasets, prompts, and evaluation configurations.
- Store enough configuration to explain how each result was produced.
- Distinguish measured values from estimated values.
- Record missing or provider-unavailable metrics as unknown rather than inventing values.
- Store the pricing version or pricing assumptions used to calculate estimated costs.
- Do not claim that an experiment is reproducible unless its inputs and relevant configuration are preserved.

## Database

- PostgreSQL is the primary relational database.
- SQLAlchemy is used for persistence.
- Alembic manages schema migrations.
- Follow existing session and transaction-management conventions.
- Review generated migrations before applying them.
- Preserve existing experiment results when schemas evolve.
- Consider data volume and query patterns when designing result storage and indexes.

## Adding a Component

Create a component only when justified by the existing architecture.

For a new domain component, use the established project conventions for models, schemas, repositories, and services where appropriate.

When changing persistence:
1. Update SQLAlchemy models.
2. Generate and review the Alembic migration.
3. Add or update persistence tests.
4. Check compatibility with existing data.

When adding an API endpoint:
1. Add request and response schemas.
2. Implement business logic in the service layer.
3. Add the endpoint and register its router.
4. Add API and business-logic tests.

Do not create unnecessary abstractions or empty files just to follow a template.

## Development Workflow

Follow the repository's OpenSpec workflow for meaningful behavioral changes.

1. Read `AGENTS.md`, relevant architecture documentation, and existing specifications.
2. Inspect the affected implementation and tests.
3. Create an OpenSpec change with a proposal, design, tasks, and relevant specification deltas.
4. Break work into small, verifiable tasks.
5. Start every new feature on a new branch created from an up-to-date `main` (for example `feat/<change-name>`), before writing any code or OpenSpec artifacts. Never work on a feature directly on `main` or reuse a previous feature's branch. Use a dedicated branch for each task or logically cohesive implementation block.
6. Implement the approved scope and add regression tests.
7. Run relevant tests, linting, formatting, and type checks.
8. Request independent code review and address valid findings.
9. When the flow is finished, commit the changes on the feature branch following the `git-conventional-commits` skill. Do not push; pushing the branch and opening a pull request happen only when the user explicitly asks.
10. Report the implementation, checks, limitations, and unresolved issues.

Do not push, merge pull requests or bypass required checks without explicit authorization.

Use a proportionate workflow for documentation-only changes and refactoring that does not change behavior.

## Code Style

- Black with an 80-character line length
- Ruff
- mypy
- pre-commit
- Pytest
- Type hints on all public functions.
- Follow established `*Schema`, `*Repository`, and `*Service` naming conventions.
- Use lowercase string values for enums, consistent with the project.
- Document non-obvious decisions rather than restating code in comments.
- Do not add dependencies without justification.

## Testing

- Use unit tests for domain logic and evaluators.
- Use mocked provider responses for deterministic provider-adapter tests.
- Use integration tests for database persistence and task execution.
- Keep real-provider tests separate and explicitly configured.
- Test timeouts, rate limits, malformed responses, retries, duplicate task delivery, and partial experiment failures.
- Verify that reported metrics match persisted data.
- Never claim tests passed unless they were actually executed successfully.

## Security

- Never commit API keys or credentials.
- Avoid logging secrets or sensitive request content.
- Validate imported datasets and external provider responses.
- Protect access to experiment data and stored credentials.
- Treat imported test cases and model outputs as untrusted data.

## Completion Report

Report:
- What changed and why.
- Files and components affected.
- Tests and quality checks executed and their results.
- Database migrations and configuration changes.
- Known limitations and remaining risks.
- Git branch and pull request status.
## Task tracking (Kan)

Tasks live in a local Kan board (http://localhost:3000, board `llm-arena`, ID `tcutdspc0hup`).
Start it with `docker compose -p kan --env-file .env.kan -f docker-compose.kan.yml up -d`.
Use `scripts/kan.py` (reads `KAN_API_KEY` from the git-ignored `.env.kan`) instead of raw API calls:

- `scripts/kan.py board` — list columns and cards
- `scripts/kan.py create "Title" -d "Description" [-l LIST]` — new card (default list `Open`; others: `In-progress`, `Review`, `Done`)
- `scripts/kan.py move CARD_ID LIST` — move a card between lists
- `scripts/kan.py show CARD_ID` / `comment CARD_ID "text"`

Board workflow:

- When work on a task starts, move its card to `In-progress`.
- When the reviewer agent has reviewed the task, move its card to `Review` (`scripts/kan.py move CARD_ID Review`) and comment with the review outcome, following the `kan-review` skill.
- Move the card to `Done` only after the pull request is merged by the user.

Never print or commit the API key.
