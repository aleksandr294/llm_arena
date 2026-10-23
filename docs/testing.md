## Quality checks

[Back to the README](../README.md)

```bash
pytest                    # integration tests need a test DATABASE_URL (below)
pytest -m "not integration"
ruff check .
black --check .
mypy
poetry run bandit -c pyproject.toml -r -ll components api admin cli.py \
  main.py taskiq_app.py migrations scripts   # medium and above must be clean
poetry run pip-audit      # needs network; see docs/security.md
pre-commit run --all-files
```

## Test database

Integration tests drop and create the component tables on `DATABASE_URL`.
They therefore refuse to run (the test fails with a clear message) unless the
database name in that URL contains `test` (case-insensitive), so exporting
your development `.env` can never wipe the development database. Create a
scratch database and point `DATABASE_URL` at it for the test run only:

```bash
docker compose exec postgres createdb -U <user> llm_arena_test
DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/llm_arena_test \
  pytest
```

The rule lives in `tests/helpers.py` (`checked_database_url`) and is used by
the root `conftest.py` and every test that builds an engine from
`DATABASE_URL`; a new test that does so must use it too.

## Real-provider tests

Tests in `tests/llm/real/` call the real OpenAI and Anthropic APIs, cost
money, and are skipped by default. To run them:

```bash
RUN_REAL_PROVIDER_TESTS=1 OPENAI_API_KEY=... ANTHROPIC_API_KEY=... \
  pytest -m real_provider tests/llm/real
```

Each test is also skipped if its provider key is unset. Override the models
with `OPENAI_TEST_MODEL` / `ANTHROPIC_TEST_MODEL`.
