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
│   └── v1/
│       ├── router.py
│       ├── utils.py
│       └── endpoints/
├── components/
│   ├── core/                    # Base classes, config, DB, BaseRepository
│   └── <component_name>/
│   │    ├── models.py
│   │    ├── schemas.py
│   │    ├── repository.py
│   │    ├── service.py
│   │    ├── tasks.py
│   └── third_party/
│       └── llm/
│           ├── providers/       # BaseLLMProvider + Anthropic / OpenAI 
│           ├── schemas.py       
│           └── service.py       # LLMService
├── migrations/                  # Alembic
├── main.py                      # python main.py API
├── taskiq_app.py                     
├── conftest.py                     
├── .env.example
└── CLAUDE.md
```
This is the intended structure, not a requirement to create every file immediately. Follow the existing repository structure if it has already been established.

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
5. Use a dedicated branch for each task or logically cohesive implementation block.
6. Implement the approved scope and add regression tests.
7. Run relevant tests, linting, formatting, and type checks.
8. Request independent code review and address valid findings.
9. Push the branch and open a pull request.
10. Report the implementation, checks, limitations, and unresolved issues.

Do not merge pull requests or bypass required checks without explicit authorization.

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