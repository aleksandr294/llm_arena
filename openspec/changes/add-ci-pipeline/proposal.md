# Proposal

## Why

Nothing runs automatically when a pull request is opened: tests, linting and
the security gates live in CLAUDE.md and are run by hand, so a failing or
vulnerable change can reach review unnoticed. Kan card LLM (CL/CD) asks for a
pull-request workflow with tests, a Bandit-style scan done through Ruff,
`pip-audit` and OWASP ZAP.

## What Changes

- Add a GitHub Actions workflow that runs on every pull request to `main`
  (and on manual dispatch) with four jobs:
  - **tests**: install with Poetry, start PostgreSQL and RabbitMQ service
    containers, run Ruff lint, Black check, mypy and the full pytest suite
    against a throwaway database whose name contains `test`.
  - **ruff-security**: the Bandit rule family (`S`) through Ruff over the
    production code.
  - **pip-audit**: audit of the locked runtime dependencies.
  - **zap**: build the image, start a throwaway instance, run the existing
    ZAP baseline and OpenAPI scans, upload the HTML/JSON reports as an artifact.
- Tests, Ruff (including the security rules) and the runtime-dependency audit
  block merging; the ZAP job reports only.
- **BREAKING (tooling)**: Bandit is removed and replaced by Ruff's `S` rules.
  `# nosec Bxxx - reason` comments become `# noqa: Sxxx - reason`, the
  pre-commit hook, the `bandit` dev dependency, `[tool.bandit]`, the
  documentation, CLAUDE.md and the suppression-reason test change accordingly.
- Out of scope: building or pushing Docker images and deployment (the example
  workflow's CD part), branch-protection settings on GitHub, scheduled scans.

## Capabilities

### New Capabilities

- `ci-pipeline`: what the pull-request workflow runs, which results block a
  merge and which are reports only.

### Modified Capabilities

- `security-scanning`: static analysis moves from Bandit to Ruff's security
  rules (scan command, suppression syntax, pre-commit hook), and the
  dependency audit and dynamic scan gain an automated run.

## Impact

- New `.github/workflows/ci.yml` (and a small helper script for the ZAP job
  if the inline shell becomes unwieldy).
- `pyproject.toml` / `poetry.lock`: `[tool.ruff.lint]` selects `S` with
  per-file ignores for tests; `bandit` dependency and `[tool.bandit]` removed.
- `.pre-commit-config.yaml`, production `# nosec` comments (about 8),
  `tests/security/test_nosec_reasons.py`.
- `README.md`, `docs/security.md`, `docs/testing.md`, `CLAUDE.md`
  (quality gates and suppression rule), findings register entries.
- Repository settings outside the code: required status checks must be set
  on GitHub by the owner for the blocking jobs to actually block.
