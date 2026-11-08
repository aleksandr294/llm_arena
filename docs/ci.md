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
| `tests` | PostgreSQL 17 and RabbitMQ 3.13 service containers, then `ruff check .`, `black --check .`, `mypy` and `pytest -q -n auto` (parallel groups, one schema each, database `arena_test`) | Yes |
| `ruff-security` | Ruff's Bandit rule family (`S`) over the production code | Yes |
| `pip-audit` | `pip-audit` over the locked runtime dependencies, plus an informational audit of the full environment | Yes (runtime step only) |
| `sqlmap` | sqlmap SQL injection scan of the string parameters of every operation in the API's OpenAPI document, against a throwaway instance; output uploaded as the `sqlmap-reports` artifact | Not yet, see below |

Blocking is a GitHub setting, not part of the workflow: in the repository's
branch protection (or ruleset) for `main`, mark `tests`, `ruff-security` and
`pip-audit` as required status checks. The `sqlmap` job fails the run on a
confirmed injection, but it is not in that list yet: mark it required only
after it has run cleanly on several pull requests, so a flaky setup step
cannot block merges. Until then a red `sqlmap` job still shows on the pull
request and should be treated as a defect.

### Run each job locally

Export the settings first (`set -a; . ./.env; set +a`), with `DATABASE_URL`
pointing at a scratch database whose name contains `test` (see
[Testing](testing.md)).

```bash
# tests
poetry run ruff check .
poetry run black --check .
poetry run mypy
poetry run pytest -q -n auto

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

# sqlmap: needs Docker and network access (PyPI, image registries);
# the scan itself took about 1.5 minutes, the whole run about 2 minutes
scripts/ci_sqlmap.sh /tmp/sqlmap-reports

# sqlmap scan stage only, against a target already running on the host
# (for checking the failure path with a deliberately vulnerable stub)
scripts/ci_sqlmap.sh --scan-only /tmp/sqlmap-reports ./openapi.json \
  localhost:8000
```

`pip` is upgraded in the audited environment because it is seeded into the
virtualenv, is not a locked dependency, and an older bundled `pip` reports
advisories that have nothing to do with the project. The full-environment
audit in the workflow (`poetry install --no-root`, then the same command) is
informational: development tooling such as `pre-commit` brings its own
transitive findings, recorded in the register in [Security](security.md).

`scripts/ci_sqlmap.sh <reports-dir>` builds the image, starts PostgreSQL,
RabbitMQ and the API on a private network with generated one-run
credentials (all containers, the network and the image are named
`ci-sqlmap-...` and removed on exit), signs in as a throwaway superadmin and
creates a little data through the API: an organization, a second user the
scan may modify, and, as an organization admin (a superadmin has no
organization), a dataset with a test case and an experiment (a placeholder
provider key lets it be created; no model is ever called). The ids and a few
dummy field values (an e-mail address, a password that matches no user) go
to `values.json` and replace the generated values of fields of that name, so
the generated requests refer to objects that exist and pass validation. The
script then runs `scripts/sqlmap_scan.sh` in a `python:3.12-slim` container
on the same network: that stage installs the pinned `sqlmap`, builds a scan
plan from `/openapi.json` with `scripts/sqlmap_plan.py`, checks each request
once (the baseline) and runs sqlmap on those that reach their handler. The
access tokens live for two hours on this instance
(`JWT_ACCESS_TTL_SECONDS=7200`) and are passed to the scan container through
the environment, not on the `docker run` command line.

Rules the scan follows:

- Only free-string parameters (query, JSON body fields, string path
  parameters) are scanned. UUID, date, enum, numeric and boolean parameters
  are validated before any SQL is built; they are listed as skipped, with the
  reason, in `plan.json`. Alternatives of an `anyOf`/`oneOf` body field other
  than the first are listed as skipped too.
- Baseline: before sqlmap runs, the unmodified request is sent once. A
  parameter counts as scanned only if that answer is 2xx, or the 401 that a
  public sign-in gives a dummy credential. Any other 4xx (validation, wrong
  scope, a missing object) means its fields never reach SQL: the parameter is
  listed as `not reached (status N)` in `reached.txt` and `plan.json`, sqlmap
  is not run for it, and the job does not fail. A 5xx or no answer at all
  fails the job. sqlmap alone would call a constant `403` a clean page; a
  constant `404` it reports as a critical error.
- Tokens: public operations use none. Authenticated ones use the superadmin
  token, or the organization admin token when the superadmin's baseline is
  not 2xx (organization-scoped operations); `reached.txt` records which.
- Public operations tolerate `401` (`--ignore-code 401`); authenticated ones
  never do, so an invalid token cannot pass as a clean scan.
- An operation with both a `*` path marker and named parameters is scanned as
  two requests.
- A run counts as complete only with sqlmap's end marker, plus either an
  injection block or sqlmap's clean verdict, and only allowlisted
  `[CRITICAL]` lines (sqlmap logs a clean verdict, a WAF heuristic, a
  heavily dynamic page and a retried request as `[CRITICAL]`). An unknown
  `[CRITICAL]` line fails the scan, so a scan that did not really run is never
  green. A retried connection is tolerated, so on a flaky target the
  time-based coverage can degrade without failing the job.
- A confirmed injection fails the job and `findings.txt` names the endpoint,
  parameter and technique. Heuristic "might be injectable" notes are kept in
  the artifact and do not fail it.
- The log shows `reached X / not reached Y of N scannable parameters`, and
  `reached.txt` lists every operation with its baseline status. The artifact
  holds `plan.json` (every parameter as scanned, not reached or skipped, with
  the reason), `findings.txt`, `scan_failures.txt`, `reached.txt` and the
  per-request sqlmap output.
- Time: each run is limited so that the whole scan fits the 60 minute job
  timeout (`SQLMAP_BUDGET`, 3000 s, divided over the rounds of four parallel
  runs; at most 25 minutes per run). A run that hits its limit counts as not
  completed.

Secrets in the artifact: sqlmap's session files hold the request, including
the `Authorization` header, so they are deleted on every way out of the
script (an `EXIT` trap, also after a failure or kill) and again by a workflow
step that runs `if: always()` before the upload. The credential check over
the reports (generated passwords, tokens, secret keys) fails the script and
removes the reports if it finds one, but it only matches the raw values, so
the deletion of the session files is the real defence and the check a
backstop. While sqlmap runs, the bearer token is visible in its process
arguments inside the scan container (throwaway; masked in the Actions log).

Supply chain: sqlmap is installed from PyPI at the exact version
`1.10.11`, without hash pinning (a compromised release would run in the
scan container, which only reaches the throwaway instance). The container
images (`python:3.12-slim`, `postgres:17`, `rabbitmq:3.13`) and the Actions
are pinned by tag, not by digest.

Limitations: the scan sees only string parameters, with generated values;
the default `--level 2 --risk 1` does not test HTTP headers, cookies or the
User-Agent, and no tamper scripts are used. Boolean-blind detection is
weak where responses rarely differ between a true and a false condition, so
detection leans on error and time-based techniques. In the last real run, 20
string parameters of 12 operations were scannable and all 20 were reached; 91
parameters were skipped (24 UUID, 18 integer, 9 enum, 8 boolean, 6 date-time
or number, 25 alternatives of `anyOf`, 1 object); the other 21 of the 33
operations take no string parameter. A clean run lowers the risk, it does not
prove the absence of injection. ZAP is no longer part of CI; the manual
procedure stays in [Security](security.md).

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
