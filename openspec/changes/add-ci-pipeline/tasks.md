# Tasks

## 1. Green baseline

- [x] 1.1 Fix the order-dependent `tests/auth/test_cli.py::test_no_http_route_creates_users` (it fails when `tests/admin/test_core.py` is collected first) without weakening `mount_admin`'s guard; verify `pytest tests/auth/test_cli.py tests/admin/test_core.py` passes in that order and alone.
- [x] 1.2 Fix the mypy error at `tests/auth/test_security.py:190`; verify `poetry run mypy` reports no errors.

## 2. Replace Bandit with Ruff security rules

- [x] 2.1 Add `"S"` to `[tool.ruff.lint] select`, exempt `tests/**` from `S` in per-file ignores, remove `[tool.bandit]` and the `bandit` dev dependency, refresh `poetry.lock`; verify `poetry run ruff check .` is clean except the known suppressions and `poetry lock --check` passes.
- [x] 2.2 Convert each production `# nosec Bxxx - reason` to `# noqa: Sxxx - reason` (B104→S104, B105→S105, B310→S310, B311→S311, B608→S608); verify `ruff check --select S components api admin cli.py main.py taskiq_app.py migrations scripts` is clean and `RUF100` reports no unused suppression.
- [x] 2.3 Rewrite `tests/security/test_nosec_reasons.py` for `# noqa: S… - reason` (include a failing-example unit test for a suppression without a reason); verify the test passes and fails on a sample without a reason.
- [x] 2.4 Replace the Bandit pre-commit hook with a Ruff hook that covers the `S` rules for production code; verify `pre-commit run --all-files` passes.
- [x] 2.5 Update `README.md`, `docs/testing.md`, `docs/security.md` (commands, the suppression rule, the findings register entry recording the tool change and a clean re-scan) and the quality-gate and suppression lines in `CLAUDE.md`; verify `pytest tests/security/test_security_doc_pointers.py` passes and no doc still names Bandit as the tool in use.

## 3. Pull-request workflow

- [x] 3.1 Add `scripts/ci_zap.sh` that builds the image, starts a throwaway network, PostgreSQL and RabbitMQ with generated credentials, migrates, creates a superadmin, starts the API, waits for readiness, runs the three documented ZAP scans, checks the reports for the token, writes them to a given directory and always cleans up; verify it runs locally end to end and leaves no containers, networks or images behind.
- [x] 3.2 Add `.github/workflows/ci.yml` with the `tests`, `ruff-security`, `pip-audit` and `zap` jobs, `permissions: contents: read`, pinned actions, per-PR concurrency, timeouts, service containers for the tests job, runtime-only blocking audit plus an informational full audit, and report artifact upload; verify with `actionlint` (or `yamllint` plus a manual read if unavailable) and by running each job's commands locally.
- [x] 3.3 Document the workflow in `docs/ci.md` (jobs, which block, how to run each locally, the job names to mark as required status checks, how to ignore an audit finding) and link it from the README; verify every command in the page was run as written.

## 4. Final checks

- [x] 4.1 Run the full local gates (pytest, ruff, black, mypy, the Ruff security command, `pip-audit`, pre-commit) and record the results, noting anything not run; verify the counts in the report match the output.

## Workflow follow-up

- Request independent review, post it on the Kan card and move the card to Review, commit with the `git-conventional-commits` skill.
- After the pull request is merged: mark `tests`, `ruff-security` and `pip-audit` as required status checks on GitHub, then archive the change.
