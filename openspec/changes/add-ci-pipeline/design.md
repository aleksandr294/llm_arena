# Design

## Context

There is no CI today. Quality gates are run by hand (`docs/testing.md`,
`docs/security.md`): pytest against a scratch PostgreSQL database whose name
contains `test` (enforced by `tests/helpers.py`), Ruff, Black, mypy, Bandit
at medium severity, `pip-audit` and manually run ZAP scans with the commands
already written in `docs/security.md`. The example workflow in
`test_ci_cd/.github/workflows/ci.yml` is a push-to-master pipeline with image
build, push and deploy, which the user excluded. The remote is GitHub
(`aleksandr294/llm_arena`). The last full local run took about 11 minutes.

## Goals / Non-Goals

**Goals:**
- One workflow file that gives reviewers the same results the developer gets
  locally, with blocking and report-only jobs clearly separated.
- Remove Bandit in favour of Ruff's `S` rules without losing the rule that
  every suppression has a reason.

**Non-Goals:**
- Docker image build/push, deployment, scheduled runs, branch protection
  configuration, caching beyond Poetry's, a test matrix over Python versions.

## Decisions

1. **One workflow, four parallel jobs** (`tests`, `ruff-security`,
   `pip-audit`, `zap`), triggered by `pull_request` to `main` plus
   `workflow_dispatch`. Parallel jobs give independent statuses that can be
   made required one by one. Alternative: a single sequential job; rejected,
   one failure would hide the others and the ZAP run is slow.

2. **Tests job runs lint, format, types and tests together.** PostgreSQL 17
   and RabbitMQ 3.13 run as service containers with health checks (same
   images as `docker-compose.yml`); `DATABASE_URL` names `arena_test`. Python
   3.12 (the Dockerfile and `requires-python` floor). Poetry is installed with
   `pip`, the virtualenv cached by `poetry.lock` hash. Everything runs through
   `poetry run`. The suite is long, so `pytest -q` without `-x`; a job timeout
   of 40 minutes.
   Two failures exist on `main` today (observed on `HEAD` d3eacfa) and would
   make the first CI run red for unrelated reasons: the order-dependent
   `tests/auth/test_cli.py::test_no_http_route_creates_users` (fastadmin is
   imported at collection by `tests/admin/test_core.py` before `mount_admin`
   configures it) and a mypy error at `tests/auth/test_security.py:190`.
   Both are fixed in this change (tests only), not deselected or ignored.

3. **Bandit through Ruff.** Add `"S"` to `[tool.ruff.lint] select`. The `S`
   rules are Ruff's port of Bandit's checks, but Ruff has no severity
   threshold, so the old "medium and above" filter becomes "no findings in
   production code". Tests are exempt from the whole family
   (`"tests/**" = ["S"]`, like Bandit's `exclude_dirs`), plus `scripts/` if
   needed. Today there are 11 findings in production code, all already
   justified with `# nosec`; they are rewritten as
   `# noqa: S105 - reason` (same ids: B104→S104, B105→S105, B310→S310,
   B311→S311, B608→S608). Because the CI step is simply `ruff check` over the
   whole repository, the security rules also run in the tests job's lint step;
   the separate `ruff-security` job runs `ruff check --select S` on the
   production paths only so a security failure is its own status.
   Ruff's `RUF100` flags unused `noqa`, which keeps suppressions honest.
   Alternatives: keep Bandit and also run Ruff `S` (two sets of suppressions;
   the user chose a full replacement); run Bandit in CI only through Ruff
   but keep the hook (rejected for the same reason).

4. **Suppression-reason test.** `tests/security/test_nosec_reasons.py` is
   rewritten for `# noqa: Sxxx - reason`, using the same production path list.
   It fails on an `S`-code `noqa` without a reason. The test file keeps its
   name only if a rename is not worth the churn; docs and CLAUDE.md are
   updated to the new meaning. Ruff's own `noqa` for other rules is not
   policed by it.

5. **Dependency audit scope.** Runtime dependencies are audited and block:
   `poetry install --only main --no-root` into a venv, then `pip-audit` run
   from a separate tool install pointing at that environment
   (`--path <site-packages>`), so dev tooling is not in scope. This avoids
   adding `poetry-plugin-export`. The known 26 findings are in dev tooling
   only (`virtualenv`, `filelock`, `certifi` via `pre-commit`), already
   accepted in the register. A full-environment audit runs as a
   `continue-on-error` step that only prints. Any runtime finding must be
   fixed or ignored by id with `--ignore-vuln` and a register entry.
   `pip-audit` failing to reach its database exits non-zero and so fails the
   job (never a silent pass).

6. **ZAP job reports only.** Steps: build the image from the `Dockerfile`,
   start postgres and rabbitmq containers on a Docker network with
   `openssl`-generated throwaway credentials, run `alembic upgrade head` and
   `cli.py create-superadmin`, start the API with `API_DOCS_ENABLED=true` and
   `API_HOST=0.0.0.0`, wait for `/api/v1/health/ready`, log in for a token
   (masked with `::add-mask::`, never echoed), then run exactly the three
   scans documented in `docs/security.md` (baseline of `/docs`, baseline of
   `/admin/` with the admin panel enabled by a throwaway `ADMIN_SECRET_KEY`,
   OpenAPI API scan with an `Authorization` replacer). `-I` makes ZAP exit 0
   on alerts, so findings do not fail the job; a failed setup does. Reports
   are uploaded with `actions/upload-artifact`, after grepping them for the
   token. The commands live in one script, `scripts/ci_zap.sh`, so they can
   be run locally and reviewed outside YAML. Alternative: the
   `zaproxy/action-baseline` action; rejected, it cannot do the authenticated
   OpenAPI scan the project already relies on, and a script keeps docs and CI
   identical.

7. **Hardening.** `permissions: contents: read` at workflow level,
   third-party actions pinned to a full version tag (checkout, setup-python,
   cache, upload-artifact), `concurrency` group per PR with
   `cancel-in-progress`, `persist-credentials: false` on checkout. No
   repository secrets are needed: all credentials are generated per run.
   Pull requests from forks get the same read-only token and no secrets.

## Risks / Trade-offs

- [Required checks are a GitHub setting, not code] → state the job names in
  the docs and the final report; the owner marks `tests`, `ruff-security`
  and `pip-audit` as required.
- [Long run time (about 11 minutes for tests, more for ZAP)] → jobs run in
  parallel; cache the Poetry virtualenv; ZAP uses the existing `-m` limits.
- [Ruff `S` is not identical to Bandit: no severity, a few checks differ] →
  accepted by the user; the register records that the tool changed and the
  production code is re-scanned once with Ruff to confirm it is clean.
- [Tests exempt from `S` hides issues in test code] → same as before with
  Bandit's `exclude_dirs`.
- [ZAP baseline noise] → report only; findings of medium risk and above go to
  the register as today.
- [Rewriting `# nosec` could drop a justification] → the rewritten test
  asserts every production `noqa: S` carries a reason, and the old 8
  comments are converted one to one.
- [CLAUDE.md quality gates change] → edited in this change, since its text
  names Bandit and `# nosec` explicitly; called out in the report.

## Migration Plan

Land the Ruff switch first (code and docs), then the workflow. Rollback is a
revert; the workflow only reads the repository. After merge the owner enables
required status checks. The workflow cannot be run before a PR exists, so
`scripts/ci_zap.sh` and the audit step are exercised locally first, and the
workflow itself is validated with `actionlint` if available and by its first
real run on the pull request.
