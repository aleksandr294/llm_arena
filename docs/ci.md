## Continuous integration

[Back to the README](../README.md)

The workflow `.github/workflows/ci.yml` runs on every pull request to `main`
and on manual dispatch (Actions tab, "Run workflow"). A push to a branch with
no pull request does not start it. It only reads the repository
(`permissions: contents: read`), needs no repository secrets, and does not
build or publish images or deploy. Third-party actions are pinned to a full
version tag, and a new push to the same pull request cancels the older run.

| Job (status check name) | What it runs | Blocks the merge |
| ----------------------- | ------------ | ---------------- |
| `tests` | PostgreSQL 17 and RabbitMQ 3.13 service containers, then `ruff check .`, `black --check .`, `mypy` and `pytest -q` (database `arena_test`) | Yes |
| `ruff-security` | Ruff's Bandit rule family (`S`) over the production code | Yes |
| `pip-audit` | `pip-audit` over the locked runtime dependencies, plus an informational audit of the full environment | Yes (runtime step only) |
| `zap` | The three OWASP ZAP scans of [Security](security.md) against a throwaway instance; reports uploaded as the `zap-reports` artifact | No, reports only |

Blocking is a GitHub setting, not part of the workflow: in the repository's
branch protection (or ruleset) for `main`, mark `tests`, `ruff-security` and
`pip-audit` as required status checks. Do not require `zap`.

### Run each job locally

Export the settings first (`set -a; . ./.env; set +a`), with `DATABASE_URL`
pointing at a scratch database whose name contains `test` (see
[Testing](testing.md)).

```bash
# tests
poetry run ruff check .
poetry run black --check .
poetry run mypy
poetry run pytest -q

# ruff-security
poetry run ruff check --select S components api admin cli.py main.py \
  taskiq_app.py migrations migrations/versions/*.py scripts

# pip-audit: runtime dependencies only, in a throwaway environment
POETRY_VIRTUALENVS_IN_PROJECT=false POETRY_VIRTUALENVS_PATH=/tmp/audit-venvs \
  poetry install --only main --no-root
ENV_PATH="$(POETRY_VIRTUALENVS_IN_PROJECT=false \
  POETRY_VIRTUALENVS_PATH=/tmp/audit-venvs poetry env info -p)"
"$ENV_PATH/bin/python" -m pip install --upgrade pip
poetry run pip-audit --path "$("$ENV_PATH/bin/python" -c \
  'import sysconfig; print(sysconfig.get_paths()["purelib"])')"

# zap: needs Docker and network access, takes several minutes
scripts/ci_zap.sh /tmp/zap-reports
```

`pip` is upgraded in the audited environment because it is seeded into the
virtualenv, is not a locked dependency, and an older bundled `pip` reports
advisories that have nothing to do with the project. The full-environment
audit in the workflow (`poetry install --no-root`, then the same command) is
informational: development tooling such as `pre-commit` brings its own
transitive findings, recorded in the register in [Security](security.md).

`scripts/ci_zap.sh <reports-dir>` builds the image, starts PostgreSQL,
RabbitMQ and the API on a private network with generated one-run
credentials, runs the baseline scans of `/docs` and `/admin/` and the
authenticated OpenAPI scan, writes the HTML and JSON reports, and removes
every container, network and image it created (all named `ci-zap-...`). ZAP
alerts do not fail it; a failed setup, an API that never becomes ready, a
scan that could not run or a credential found in a report does.

### Ignore an audit finding

A finding in a runtime dependency fails the `pip-audit` job. Fix it by
upgrading the package (`poetry update <package>`) when a fixed version
exists. Otherwise ignore the advisory by id, in the blocking step of the
workflow, and say why:

```yaml
"$RUNNER_TEMP/audit-tool/bin/pip-audit" --path "$SITE_PACKAGES" \
  --ignore-vuln GHSA-xxxx-xxxx-xxxx
```

Every ignored id needs a row in the findings register in
[Security](security.md) (the advisory, the package, why it does not apply or
cannot be fixed yet, and when to revisit it). If `pip-audit` cannot reach its
vulnerability database it exits non-zero and the job fails; that is a failed
run, never a pass.
