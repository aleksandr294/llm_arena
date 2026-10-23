# Tasks

## 1. Static analysis

- [x] 1.1 Add `bandit` and `pip-audit` as dev dependencies, add `[tool.bandit]` to `pyproject.toml` (production paths, medium severity and above, tests excluded), add a pre-commit hook, create `docs/security.md` with the commands and an empty findings register, and add a test that fails when a production `# nosec` has no reason; verify `poetry install`, the hook runs, and the suppression test passes and fails on a synthetic violation
- [x] 1.2 Run Bandit, record every finding in the register, and resolve them: replace the production `assert` statements with explicit checks (with tests), keep the jitter `random` with a reasoned `# nosec`, change the default `API_HOST` to `127.0.0.1` with `API_HOST: 0.0.0.0` set in the Compose `api` service and documented in `.env.example` and README, validate the URL scheme in `scripts/kan.py`, justify the placeholder constant; verify a clean Bandit run and the full unit suite

## 2. Dependencies and lint

- [x] 2.1 Run `poetry run pip-audit`, upgrade vulnerable packages where fixed versions exist (`poetry update` limited to those packages, then the full suite) and record accepted exceptions with reason in the register; verify the audit output is clean or fully explained, or record it as not run when the database is unreachable
- [x] 2.2 Evaluate stricter Ruff rule sets (`B`, `UP`, `SIM`, `ASYNC`, `PT`, `RUF`), enable those that pass cleanly or with justified per-file ignores, fix what they find without behavior change, and verify `ruff`, `black`, `mypy` and the full suite

## 3. Untrusted content

- [x] 3.1 Add `tests/security/` with a shared hostile corpus and tests against every sink (prompt rendering including an AST purity check, runner with a fake provider returning hostile output, API round trip, JSON and CSV export, admin panel JSON endpoints, logs, evaluators with deeply nested and very large inputs, cross-tenant references), and verify they pass and that disabling a protection (for example CSV neutralization) makes the relevant test fail
- [x] 3.2 Add `MAX_STORED_RESPONSE_CHARS` (default 1,000,000), truncate the stored response with a visible marker in the runner, add `experiment_results.response_truncated` (migration `0007`, result schema and export columns), and verify tests for exact-limit, over-limit and unaffected short responses, the migration up and down on a scratch database with existing rows, and `alembic check` without drift

## 4. HTTP and container hardening

- [x] 4.1 Add the pure ASGI middleware in `api/middleware.py` (security headers, optional HSTS, `Cache-Control: no-store` for auth and credentialed requests, `MAX_REQUEST_BODY_BYTES` enforcement for declared and chunked bodies) with settings, `.env.example` entries and wiring in `create_app`, and verify tests for headers on success and error responses, HSTS toggle, no-store rules, 413 for declared, chunked and exact-limit bodies, normal requests unaffected, and that Swagger UI and the admin panel still load
- [x] 4.2 Harden the Dockerfile (unprivileged user) and `docker-compose.yml` (database and broker ports on `127.0.0.1`, `no-new-privileges` for `api` and `worker`), and verify `docker compose config`, a real image build, and a smoke test of the built image on a spare port with a scratch database showing the process is not root and health, docs and login work, without touching the running dev containers

## 5. OWASP review and dynamic scan

- [x] 5.1 Write the OWASP Top 10 (2021) and API Security Top 10 (2023) table in `docs/security.md` with evidence pointers to code and tests for every item, and verify each pointer resolves
- [x] 5.2 Run the ZAP baseline and OpenAPI-driven scans against a locally started throwaway instance (container, spare port, scratch database, throwaway credentials, authenticated header), record each alert of medium risk or higher in the register as fixed or accepted, fix the fixable ones with tests, and verify a re-run shows them resolved; record exactly what could not be run

## 6. Code review

- [x] 6.1 Run independent read-only reviews of the whole code base in five slices against the best-practice checklist in design.md, merge the findings into the register with severity and location, and verify every slice report was received and triaged
- [x] 6.2 Fix every High and Medium finding with regression tests where applicable, create Kan cards for the Low findings and the login rate-limiting follow-up, and verify the full suite and quality checks

## 7. Documentation and final run

- [x] 7.1 Complete `docs/security.md`, add the new commands (Bandit, pip-audit, ZAP) to the README Commands block and the quality-gate lines to CLAUDE.md, and verify the documented commands run as written
- [x] 7.2 Run everything: `pytest` (including integration tests), `ruff`, `black --check`, `mypy`, Bandit, pip-audit, migrations up and down on a scratch database, `openspec validate --specs`, `docker compose config` and the image smoke test, and verify all pass, listing anything that could not be run (for example real-provider tests without keys)

## Workflow follow-up

- Move Kan card LLM-11 (`zl08e9z3jwsk`) to In-progress when work starts and to Review once the reviewer agent has reviewed it (see the `kan-review` skill).
- Work on branch `chore/final-quality-audit` (from `main`), commit per batch with the `git-conventional-commits` skill, do not push without being asked, and archive the change after approval.
