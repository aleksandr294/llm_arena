# Tasks

## 1. Scan plan generator

- [x] 1.1 Add `scripts/sqlmap_plan.py` (stdlib only): read an OpenAPI document, write one raw request file per operation and `plan.json` listing every parameter as scanned or skipped with the reason (free strings scanned; UUID, date, enum, numeric and boolean skipped); verify with unit tests under `tests/` that cover path, query and JSON-body parameters, skipped types and an operation added to the document.
- [x] 1.2 Add `parse_findings` to the same module that extracts confirmed injections (endpoint, parameter, technique) from sqlmap output and ignores heuristic-only notes; verify with unit tests on captured sample output including a clean run, a confirmed injection, a heuristic-only note and unparseable output (treated as a failure).

## 2. Scan script

- [x] 2.1 Replace `scripts/ci_zap.sh` with `scripts/ci_sqlmap.sh <reports-dir>`: keep the throwaway-instance setup and cleanup, seed an organization, dataset, test case and admin user through the API, generate the plan from `/openapi.json`, run sqlmap (pinned `sqlmap==1.10.11` in a `python:3.12-slim` container on the private network, options per design) with up to four parallel requests, strip sqlmap session files and run the credential check over the reports, and exit non-zero on a confirmed injection, a failed scan or a missing result; verify it runs end to end against the real API with exit 0 and no leftover containers, networks or images.
- [x] 2.2 Prove the failure path with a deliberately vulnerable stdlib stub kept outside the repository (scratch directory): the scan stage pointed at it exits non-zero and names the parameter; also run once with a deliberately broken target to confirm a setup failure is non-zero; record the measured run time of the real scan and tune `--level`, parallelism or the job timeout if it exceeds the budget.

## 3. Workflow and documentation

- [x] 3.1 Replace the `zap` job in `.github/workflows/ci.yml` with a `sqlmap` job (checkout, run `scripts/ci_sqlmap.sh`, upload `sqlmap-reports` with `if: always()`, 60 minute timeout, same permissions and pinned actions); verify the file parses with `yaml.safe_load`, that no `zap` reference remains in the workflow, and read it against the ci-pipeline spec.
- [x] 3.2 Update `docs/ci.md` (job table, local command, the recommendation to require `sqlmap` after stable runs, limitations), `docs/security.md` (overview table, dynamic scan section: ZAP is manual, sqlmap runs in CI; findings register entry for the tool change and the first clean run), and `README.md`; verify `pytest tests/security/test_security_doc_pointers.py` passes and every command in `docs/ci.md` was run as written.

## 4. Final checks

- [x] 4.1 Run `ruff check .`, `black --check .`, `mypy`, the Ruff security command and the full test suite the way CI does, `pytest -q -n auto` (parallel groups, one schema each; scratch database whose name contains `test`, no `.env`); verify the results match the output and record anything not run.

## Workflow follow-up

- Request independent review, post it on the Kan card and move the card to Review, commit with the `git-conventional-commits` skill (pull request targets `feat/ci-pipeline` until CI is in `main`).
- After the pull request is merged: decide whether to mark `sqlmap` as a required status check, then archive the change.
