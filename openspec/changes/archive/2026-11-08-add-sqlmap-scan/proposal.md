# Proposal

## Why

The pull-request workflow (`add-ci-pipeline`) ends with an OWASP ZAP job.
Kan card "Добавить CL/CD" was edited on 2026-11-05 to ask for sqlmap instead:
the dynamic check the project wants on every pull request is SQL injection
testing of the API, not a broad scanner. ZAP's report-only baseline rarely
tests request bodies deeply and its output is noisy, while sqlmap reports a
confirmed injectable parameter or nothing.

## What Changes

- **Replace the `zap` CI job with a `sqlmap` job.** The job starts the same
  throwaway instance (image built from the repository, scratch PostgreSQL and
  RabbitMQ, generated credentials), signs in as a throwaway superadmin and
  runs sqlmap against the operations listed in the application's own OpenAPI
  document: query parameters and JSON body fields, public and authenticated.
- **The job fails on a confirmed injection** and on any setup or scan failure;
  it uploads the sqlmap output as a workflow artifact with no credential in it.
- The scan plan is generated from `/openapi.json`, so a new endpoint is
  covered without editing the workflow. Parameters that the API validates as a
  non-string type (UUID, integer, boolean, enum) cannot carry a payload and
  are listed as skipped with the reason, never silently dropped.
- Remove `scripts/ci_zap.sh` and its job. The manual ZAP procedure in
  `docs/security.md` stays as a documented, local-only check.
- **Not changed**: tests, Ruff `S`, `pip-audit` jobs, the required status check
  names `tests`, `ruff-security`, `pip-audit`.
- Out of scope: other sqlmap targets (admin panel UI, form posts), WAF/tamper
  scripts, sqlmap exploitation features (data dumping, shells), scheduled
  scans, pinning the sqlmap package by hash.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ci-pipeline`: the dynamic scan requirement becomes a SQL injection scan
  with sqlmap that fails on a confirmed finding; the pull-request workflow
  requirement names the new job.
- `security-scanning`: the dynamic scan requirement keeps ZAP as the manual,
  documented scan and adds the automated sqlmap run in the pull-request
  workflow.

## Impact

- `.github/workflows/ci.yml`: `zap` job replaced by `sqlmap`; artifact
  `sqlmap-reports` instead of `zap-reports`.
- `scripts/`: `ci_zap.sh` replaced by `ci_sqlmap.sh` (reusing its throwaway
  instance setup) and a stdlib-only plan generator `sqlmap_plan.py`.
- Tests for the plan generator and for the sqlmap result parser.
- `docs/ci.md`, `docs/security.md` (scan description, findings register),
  `README.md`.
- The change is stacked on branch `feat/ci-pipeline` (CI is not yet in
  `main`); its pull request targets that branch until CI is merged.
- Runtime cost: sqlmap is installed from PyPI at a pinned version inside the
  scan container on each run, so the job needs network access to PyPI.
