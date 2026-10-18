# Proposal

## Why

Kan card LLM-11 ("Финальное тестирование"). The MVP feature set is complete and merged. Before calling it done the project needs one structured pass that reviews the whole code base against best practices, runs every test, adds a static security scanner (Bandit), covers what Bandit cannot see with an OWASP-based review and a dynamic scan, and checks how stored and model-generated text is handled with respect to prompt injection. Today none of this is automated, and recurring reviewer notes (security headers, request limits, container hardening, no scanner in the toolchain) have been deferred.

## What Changes

- Add Bandit (static security analysis) and a dependency vulnerability audit to the dev toolchain, with configuration in `pyproject.toml`, a pre-commit hook, a documented command set, and a findings register in `docs/security.md`. Triage every finding: fix it or suppress it inline with a justification.
- Perform an OWASP review (Top 10 and API Security Top 10) mapped to the code with evidence, and run a dynamic OWASP ZAP scan (baseline plus OpenAPI-driven) against a locally started instance with throwaway data. Triage the alerts.
- Add HTTP hardening that these checks call for: standard security response headers, `Cache-Control: no-store` for authentication responses, a request body size limit, and container hardening (non-root image user, database and broker ports bound to localhost in Compose).
- Add a prompt-injection and untrusted-content test suite: dataset content, prompt templates and model output are adversarial data, never executed or interpreted, in every sink (prompt rendering, storage, API, exports, admin panel, logs, evaluators).
- Run an independent whole-code review against best practices (layering from CLAUDE.md, async correctness, error handling, typing, dead code, docs consistency) and fix the High and Medium findings; evaluate stricter Ruff rule sets.
- Run all tests (unit, integration, migrations up and down on a scratch database, container image build and smoke test) and record exactly what could not be run.

## Capabilities

### New Capabilities
- `security-scanning`: the static analysis, dependency audit and dynamic scan that must pass, and how findings are tracked.
- `untrusted-content-handling`: datasets, templates and model output are treated as data everywhere, with prompt-injection resilience guarantees.
- `http-hardening`: security headers, cache control for sensitive responses, request size limits and container hardening.

### Modified Capabilities

## Impact

- New dev dependencies `bandit` and `pip-audit` (justified in design.md); `[tool.bandit]` and possibly Ruff rule changes in `pyproject.toml`; new pre-commit hook; `docs/security.md`; README Commands and CLAUDE.md quality-gate lines.
- A small ASGI middleware in `api/` for headers and body limits; Dockerfile and `docker-compose.yml` hardening; replacement of production `assert` statements with explicit checks where Bandit flags them; whatever fixes the review and scans produce.
- New tests for untrusted content and hardening. One small migration (`0007`) adds a `response_truncated` flag to results for bounded stored output.
- Branches from `main`, which already contains all earlier changes.

## Assumptions

Recorded in design.md and revisable before apply: scans run against a local throwaway instance, never the developer's running stack or real data; ZAP runs in a container (network access to pull the image is needed and noted if unavailable); findings from the review and scans that need a new feature (notably login rate limiting, which needs shared state) are recorded with a follow-up Kan card instead of being built here; real-provider tests are not run (no keys); prompt injection into a model under evaluation is the intended use of the platform, so the guarantee is about blast radius and data handling, not about making models resistant.

## Out of Scope

- Login or per-account rate limiting and account lockout (follow-up card), a web application firewall, TLS termination, and secret-manager integration.
- LLM-as-a-judge (does not exist yet; its injection risks are noted for when it is built).
- Rewriting working code only for style where no best-practice finding justifies it.
- Penetration testing of the developer's real stack or of third-party services.
