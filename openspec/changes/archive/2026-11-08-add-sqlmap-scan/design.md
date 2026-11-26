# Design

## Context

On branch `feat/ci-pipeline` (CI not merged to `main` yet) the `zap` job runs
`scripts/ci_zap.sh`: it builds the image, starts PostgreSQL 17 and RabbitMQ on
a private Docker network with generated credentials, migrates, creates a
superadmin, starts the API, waits for `/api/v1/health/ready`, logs in from
inside the API container (token held in a shell variable, masked on Actions),
runs three ZAP scans and greps the reports for credentials. See `proposal.md`
for why sqlmap replaces ZAP. The API has 33 operations: 11 with a JSON body,
9 with query parameters, 19 with path parameters; the OpenAPI document is
served at `/openapi.json` when `API_DOCS_ENABLED=true`. Request values are
validated by Pydantic/FastAPI before any SQL is built.

## Goals / Non-Goals

**Goals:**
- A failing job whenever sqlmap confirms an injectable parameter, and never a
  green job because the scan did not run.
- Coverage that follows the API automatically (generated from OpenAPI).
- Reuse the throwaway-instance setup that already works.

**Non-Goals:**
- Exploiting findings (no `--dump`, `--os-shell`, `--sql-shell`), tamper
  scripts, scanning the admin panel UI, scheduled runs.

## Decisions

1. **Script layout.** `scripts/ci_zap.sh` is replaced by
   `scripts/ci_sqlmap.sh <reports-dir>`: build, containers, readiness waits,
   migration, superadmin, login, seeding, cleanup trap and the credential
   grep over the reports. The scan stage is a separate script,
   `scripts/sqlmap_scan.sh`, that runs inside the `python:3.12-slim`
   container and installs sqlmap there. `ci_sqlmap.sh --scan-only
   <reports-dir> <openapi.json> <host:port>` runs only that stage against a
   target on the host; it exists for local verification of the failure path.
   Alternative: one script with a `--tool` switch; rejected, ZAP leaves CI and
   the manual procedure stays documented in `docs/security.md`.

2. **Scan plan from OpenAPI.** A stdlib-only `scripts/sqlmap_plan.py` reads
   `/openapi.json` and writes one raw HTTP request file per operation
   (`-r` format) plus a `plan.json` listing, for every parameter, whether it is
   scanned or skipped and why. A parameter is scanned when its schema is a
   free string (no `format: uuid|date-time|date`, no `enum`, no numeric or
   boolean type), because anything else is rejected by validation before it
   can reach SQL. Skipped parameters are written to `plan.json` and to the
   artifact with the reason, so coverage is auditable. Path parameters are filled
   from `path_values.json` (seeded ids), else a fixed random UUID; a string
   path parameter gets an explicit `*` marker, because sqlmap tests a path
   segment only there. Request bodies are built from the schema (`"x"` for
   strings, `1` for integers, valid UUID/enums from the schema) so they pass
   validation and reach the handler; sqlmap tests each named JSON field
   itself.
   Alternative: a hand-written endpoint list; rejected, it rots.

3. **Seed minimal data first.** The script creates through the REST API an
   organization, and then, as an organization admin created for the purpose
   (a superadmin has no organization and cannot own datasets), one dataset
   with one test case. The ids are written to `path_values.json`, which the
   plan uses for path parameters. The access token lives 7200 s on the
   throwaway instance (`JWT_ACCESS_TTL_SECONDS=7200`) so a long scan does not
   outlive it. Experiments need real LLM providers and are not seeded.

4. **Running sqlmap.** In a `python:3.12-slim` container on the same
   private network (no host port), with `pip install sqlmap==1.10.11` (pinned)
   at start. Options per request file: `--batch --level 2 --risk 1 --dbms
   PostgreSQL --timeout 15 --retries 1 --threads 1 --flush-session
   --disable-coloring`, and `--headers "Authorization: Bearer <token>"` for
   authenticated operations. The token reaches the container through the
   environment (`-e SCAN_TOKEN`), not the `docker run` command line. Public
   operations use `--ignore-code 401` only; operations whose path object
   cannot be seeded (`experiment_id`) also use `--ignore-code 404`, so their
   coverage is best effort. An operation that has both a `*` path marker and
   named parameters is scanned as two requests. `--risk 1` avoids heavy and
   write-capable payloads; no exploitation switches. Up to four requests run
   in parallel (`xargs -P 4`), each with its own output directory, and the job
   timeout is 60 minutes. Measured: the real scan took about 78 s, and 12 of
   the 33 operations have scannable parameters.
   Alternatives: install sqlmap on the runner and publish a host port
   (rejected: breaks the private-network design); an unofficial Docker Hub
   image (rejected: unpinned third-party image).

5. **Detecting a finding.** sqlmap exits 0 whether or not it finds anything,
   so each request's output is parsed (`parse_findings` and the `findings`
   command of `sqlmap_plan.py`, unit tested on captured output) for the
   `sqlmap identified the following injection point(s)` block with its
   `Parameter: <name> (<place>)` lines. `findings.txt` lists endpoint,
   parameter and technique and the script exits non-zero when it is non-empty.
   Completeness rule: a run counts only with sqlmap's end marker plus either
   an injection block or sqlmap's clean verdict, and with only allowlisted
   `[CRITICAL]` lines; an unknown `[CRITICAL]` line fails closed, as does a
   crash, missing output or setup failure. Heuristic "might be injectable"
   notes stay in the artifact and do not fail the job.

6. **Artifact and secrets.** `sqlmap-reports` contains `plan.json`,
   `findings.txt` and the per-request sqlmap output directories. The token is
   passed only through the `--headers` argument and is stripped from sqlmap's
   own session files by deleting `target.txt`/`session.sqlite` before upload
   (they hold headers and request data); the existing credential `grep` over
   the whole reports directory then must pass.

7. **Verification without a real vulnerability.** The detection path is
   proven locally against a deliberately vulnerable stdlib stub kept in the
   scratch directory (not committed): the `--scan-only` stage pointed at it
   fails with that finding, and against the real API it passes. Committing a
   vulnerable app would also trip Ruff `S608` and invite misuse.

8. **Failing vs reporting.** A confirmed SQL injection is a real defect with a
   very low false-positive rate, so the job fails (unlike ZAP, which only
   reported). Marking `sqlmap` as a required status check is still a GitHub
   setting for the owner; `docs/ci.md` recommends adding it once the job has
   run cleanly on a few pull requests. This is an assumption recorded here
   because the card does not say.

## Refinements after review

Review of the first implementation found that a scan could be green while
testing nothing; the following replace or extend the decisions above.

9. **Baseline before sqlmap.** Every planned request is sent once, unmodified,
   with the token it will use. Only a 2xx (or, for public operations, the 401
   a sign-in gives a dummy credential) counts as reached; any other 4xx marks
   its parameters `not reached (status N)` in `reached.txt` and `plan.json`
   and sqlmap is skipped for them, without failing the job (an object that
   cannot be seeded is a coverage gap, not a defect); a 5xx or no answer fails
   the job. Measured with sqlmap 1.10.11: a constant 403 is reported as "not
   injectable" (false assurance) and a constant 404 as a critical error, so
   sqlmap's own verdict cannot stand in for this check. The log and the
   artifact state "reached X / not reached Y of N scannable parameters".
10. **Two tokens, per-operation choice.** A superadmin has no organization, so
    organization-scoped operations answer 403/404 to it. The script holds the
    superadmin and the seeded organization admin token and uses, per
    operation, the first whose baseline is 2xx. Authenticated operations never
    ignore 401.
11. **Seeded values, not only ids.** `values.json` (ids of the seeded
    organization, dataset, second user and experiment, plus dummy field
    values) replaces generated values by field name in path, query and body,
    with `METHOD /path:name` keys for one operation. This is what lets
    experiment, evaluation, user and organization operations reach their
    handlers. Creating an experiment needs a configured provider: the
    throwaway instance gets a placeholder provider key and no model is called.
12. **Secrets on every exit.** sqlmap session files (they hold the
    `Authorization` header) are deleted in an `EXIT` trap of the scan stage,
    again in the `EXIT` trap of `ci_sqlmap.sh` (which also runs the credential
    grep) and by an `if: always()` workflow step before the upload. The
    credential grep only matches raw values, so deletion is the defence. The
    per-run time limit is derived from a 3000 s budget over the rounds of
    parallel runs so the scan fits the 60 minute job timeout.
13. **Completeness.** sqlmap logs a clean verdict, a WAF heuristic, a heavily
    dynamic page and a retried request as CRITICAL while still finishing; only
    those are allowlisted and an unknown CRITICAL fails closed. Retried
    connections are tolerated, so time-based coverage can degrade without a
    failure. Level 2 / risk 1 do not test headers, cookies or the User-Agent.
14. **Untrusted text.** sqlmap output and OpenAPI names are untrusted: the
    parser ignores very long lines, requires the injection header before any
    parameter, and request files and the job list are built from quoted or
    sanitised names. Covered by tests with the shared hostile corpus and by
    tests of the shell exit-code logic with a fake sqlmap.

## Risks / Trade-offs

- [Run time: 33 operations × several techniques] → scan only free-string
  parameters, `--level 2`, `--risk 1`, fixed DBMS, four parallel runs, 60 minute
  timeout; measured about 78 s locally.
- [Weak boolean-blind detection: values are `x` and responses rarely differ
  between true and false conditions, so detection leans on error and
  time-based techniques; a clean run is evidence, not proof] → stated in
  `docs/security.md`; ZAP stays as a manual complement.
- [False negatives: only the superadmin view, thin seed data, experiments
  best effort, 12 of 33 operations scannable] → documented as a limitation; ZAP stays available as the manual
  complement.
- [sqlmap output format changes between versions] → version pinned; parser
  covered by tests on captured output; the completeness rule fails a run
  with no end marker or an unknown `[CRITICAL]` line.
- [PyPI availability or a compromised release] → exact version pin; install
  failure fails the job; hash pinning left as a follow-up.
- [Throwaway token inside sqlmap session files] → files deleted
  before upload and the credential grep guards the artifact.
- [Stacked branch] → the pull request targets `feat/ci-pipeline`; it is
  retargeted to `main` after CI is merged.

## Migration Plan

Replace the job and script in one commit set, update docs and register. No
data or schema change. Rollback is a revert; the previous ZAP job returns with
it. Required status checks on GitHub need no change (the new job is not in the
list unless the owner adds it).
