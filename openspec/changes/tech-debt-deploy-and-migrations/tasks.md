# Tasks

Branch: `feat/tech-debt-deploy-and-migrations` (worktree `../llm-arena-tech-debt-deploy`).
Kan cards: `ofvt04oeml0t` (PRIORITY), `1oq69hj67huw`. Start with group 1 (priority card).

## 1. Compose and container

- [x] 1.1 Rewrite `x-app-environment` with required `${VAR:?}` and defaulted optional variables (design decision 1), add the named volumes and the worker healthcheck; verify `docker compose --env-file .env.example config` fails without `JWT_SECRET_KEY` and renders every variable otherwise.
- [x] 1.2 Add `tests/ci/test_compose.py` (PyYAML, declared as an explicit dev dependency because it is imported directly; it is already in the lock via pre-commit) asserting: every `SettingsSchema` field is passed or listed as intentionally not passed, required variables use `:?`, named volumes exist for postgres and rabbitmq, the worker has a healthcheck; verify it fails when a variable is removed from the compose file.
- [x] 1.3 Bring the stack up from a fresh `.env` built from `.env.example` with real-looking secrets (`docker compose up --build -d`), confirm api and worker healthy, `down` then `up` keeps a created dataset, and `PRICING_FILE`/`LLM_MAX_CONCURRENCY` are visible with `docker compose exec worker env`; record the real output (or "not run" if Docker is unavailable).
- [x] 1.4 Pin Poetry (`ARG POETRY_VERSION=2.5.1`) and the Python base image patch tag in the `Dockerfile`; add a test that the Dockerfile's Poetry version equals `POETRY_VERSION` in `.github/workflows/ci.yml`; verify `docker build` succeeds and the container still runs as uid 10001.
- [x] 1.5 Replace `*` constraints in `pyproject.toml` with caret ranges from the lock; verify `poetry check --lock` passes, `poetry.lock` versions are unchanged and `poetry run pip-audit` is clean (or recorded as not run when offline).
- [x] 1.6 Update README (compose variables, `PRICING_FILE` mount example, volumes and `down -v`), `.env.example` comments and `docs/security.md` (pinned inputs, digest follow-up); verify the README commands run as written.

## 2. Schema and migrations

- [x] 2.1 Add the two model indexes and migration `0010` with upgrade/downgrade; add `tests/migrations/test_0010_fk_indexes.py` using the shared migration helpers (indexes exist after upgrade, gone after downgrade, existing rows untouched); verify against the scratch database.
- [x] 2.2 Add `tests/migrations/test_round_trip.py` (head then base, no application tables left) and `tests/migrations/test_single_head.py`; verify the single-head test fails on a temporary duplicate parent.
- [x] 2.3 Set `compare_type=True` in `migrations/env.py` (online and offline) and run the drift helper at head; fix model/migration differences it reveals or record each accepted one with a reason; verify the drift test passes.
- [x] 2.4 Remove the dead logging sections from `alembic.ini`; verify `alembic current` and `alembic upgrade head` output is unchanged against the scratch database.
- [x] 2.5 Update `docs/migrations.md` (revision table `0007`-`0010`, `alembic heads`, volume migration note, index lock note), README migration lines and `docs/testing.md` (Ruff S rules, pip-audit); verify every documented command runs as written.

## 3. Integration

- [x] 3.1 Run `pytest` (serial and `-n auto`), `ruff check`, `black --check`, `mypy`, the Ruff S security rules (Bandit was replaced), `pip-audit` (or record not run) and `openspec validate tech-debt-deploy-and-migrations --strict`; report real results.

## Workflow follow-up

- Move the two Kan cards to `In-progress` at start and to `Review` after the reviewer agent.
- HARD merge precondition: re-parent `0010` onto `0009` (`down_revision`) and confirm `alembic heads` shows exactly one head before merging. The revision table in `docs/migrations.md` describes `0008` and `0009` and is only correct once those changes have merged.
- Deviation: the compose default `API_DOCS_ENABLED:-true` equals the current application default; flip it to `false` (and the application default with it) when `tech-debt-core-api-cleanups` merges.
- Archive the change after review requirements are satisfied.
