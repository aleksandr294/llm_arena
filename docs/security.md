# Security checks

[Back to the README](../README.md)

## Overview and how to run the checks

Security is checked in layers: static analysis, a dependency audit, tests
for hostile input and for the HTTP and container hardening, a dynamic scan,
an OWASP review and a code review. Every finding is in the
[findings register](#findings-register) with its outcome, what is accepted
is in [accepted risks](#accepted-risks), and what is still open is in
[known gaps and follow-ups](#known-gaps-and-follow-ups).

| Check | How to run | Notes |
| ----- | ---------- | ----- |
| Bandit | see below | Medium severity and above must be clean; also a pre-commit hook |
| pip-audit | `poetry run pip-audit` | Needs network access |
| Hostile-corpus tests | `poetry run pytest -q tests/security` | No database needed for most; see the last section |
| Doc pointer test | `poetry run pytest -q tests/security/test_security_doc_pointers.py` | Fails when a path or test cited in the OWASP tables no longer exists |
| Suppression reasons | `poetry run pytest -q tests/security/test_nosec_reasons.py` | Fails on a production `# nosec` without a reason |
| Container smoke test | [Container hardening](#container-hardening) | Throwaway network, database and port |
| ZAP baseline and API scan | [How to run the ZAP scans](#how-to-run-the-zap-scans) | Throwaway instance only |

Export the settings first (`set -a; . ./.env; set +a`) for the tests, as in
the README.

### Static analysis (Bandit)

Configuration is `[tool.bandit]` in `pyproject.toml` (production code only,
medium severity and above):

```bash
poetry run bandit -c pyproject.toml -r -ll components api admin cli.py \
  main.py taskiq_app.py migrations scripts
```

It also runs as the `bandit` pre-commit hook. A suppression must be an inline
comment with a reason, `# nosec B311 - reason`; the test
`tests/security/test_nosec_reasons.py` fails otherwise.

### Dependency audit (pip-audit)

Needs network access to the vulnerability database; if it cannot be reached,
record the audit as not run, never as passed:

```bash
poetry run pip-audit
```

Result on 2026-10-18 (database reachable): `No known vulnerabilities found`.
No package was upgraded and no exception was needed.

## HTTP hardening

One pure ASGI middleware (`api/middleware.py`) wraps the whole app,
including the mounted admin panel and error responses:

- Every response carries `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.
- `Strict-Transport-Security: max-age=<HSTS_MAX_AGE_SECONDS>` is added only
  when that setting is above 0 (default 0, off); enable it only behind HTTPS.
- `Cache-Control: no-store` is set on `/api/v1/auth/*`, on the admin
  panel's `/admin/api/sign-in` and `/admin/api/sign-out`, and on every response
  to a request with an `Authorization` header or the admin session cookie
  (`admin_session_id`).
- Every response carries a `Content-Security-Policy`. Anything that is not an
  HTML document (JSON, CSV, errors, 413, static assets) gets
  `default-src 'none'; base-uri 'none'; form-action 'none';
  frame-ancestors 'none'`. The HTML pages get a page policy chosen by path
  (`/docs*` Swagger UI, `/redoc*` ReDoc, `/admin*` panel): scripts only from
  `'self'` or `https://cdn.jsdelivr.net`, `connect-src 'self'`, images from
  `'self'`/`data:` (Swagger UI and ReDoc also from `fastapi.tiangolo.com`),
  ReDoc also Google Fonts and `worker-src blob:`. The pages' inline scripts
  (Swagger UI bootstrap, the panel's `window.SERVER_URL`) are allowed by
  SHA-256 hash of their exact text, computed from the served HTML, so scripts
  never need `'unsafe-inline'` or `'unsafe-eval'`. `style-src` does keep
  `'unsafe-inline'` (Swagger UI and the panel's UI library set inline
  styles, which cannot be hashed); a scanner may still report that, and it is
  accepted because script execution is what the policy protects. If the
  CDN, Swagger UI or fastadmin change what a page loads, update the
  constants in `api/middleware.py`; the tests parse the served pages and fail
  when a script or stylesheet origin or inline script is not covered. The
  policies were verified against the served HTML and static bundle, not in a
  real browser.
- Bodies above `MAX_REQUEST_BODY_BYTES` (default 2 MiB, exactly the limit is
  allowed) get `413` with `{"detail": "Request body too large"}`: at once
  for a declared `Content-Length`, and for chunked bodies as soon as the
  counted bytes pass the limit, before the application reads more.

- An unhandled exception answers `500` with `{"detail": "Internal Server
  Error"}` from the middleware itself, so that response carries the same
  headers. The traceback is logged server-side with `logger.exception`.
  Exception messages from pydantic or SQL may embed request-derived text, so
  server logs must be treated as sensitive (see accepted risks).

Swagger UI, ReDoc and the panel are covered by tests and keep working.

## Container hardening

- The `Dockerfile` creates the system user and group `app` (uid/gid 10001,
  `/usr/sbin/nologin`, no home directory) and ends with `USER 10001:10001`
  before `CMD`. Application files stay root-owned and not writable by the app
  user; nothing at runtime writes to the image (bytecode writing is off, the
  pip/poetry cache is only used at build time as root).
- `docker-compose.yml` sets `security_opt: ["no-new-privileges:true"]` for
  `api` and `worker` (through the shared `x-app` anchor), and publishes
  PostgreSQL (`127.0.0.1:5432`) and RabbitMQ (`127.0.0.1:5672`,
  `127.0.0.1:15672`) on the loopback interface only. The API stays on
  `8000:8000`.

Smoke test of the built image without touching the dev stack (use unique
names, a spare host port and throwaway credentials; remove them afterwards):

```bash
docker build --load -t llm-arena:audit-smoke .
docker network create audit-smoke-net
docker run -d --name audit-smoke-pg --network audit-smoke-net \
  -e POSTGRES_USER=sm -e POSTGRES_PASSWORD=smpw -e POSTGRES_DB=sm postgres:17
ENVS="-e JWT_SECRET_KEY=<throwaway> \
  -e DATABASE_URL=postgresql+asyncpg://sm:smpw@audit-smoke-pg:5432/sm \
  -e RABBITMQ_URL=amqp://x:x@audit-smoke-nobroker:5672/ -e API_HOST=0.0.0.0"
docker run --rm --network audit-smoke-net $ENVS llm-arena:audit-smoke \
  alembic upgrade head
docker run -d --name audit-smoke-api --network audit-smoke-net $ENVS \
  --security-opt no-new-privileges:true -p 127.0.0.1:18765:8000 \
  llm-arena:audit-smoke
docker exec audit-smoke-api id -u          # 10001, not 0
curl -i http://127.0.0.1:18765/api/v1/health/live   # 200 plus headers
curl -i http://127.0.0.1:18765/docs                 # 200
docker rm -f audit-smoke-api audit-smoke-pg
docker network rm audit-smoke-net; docker rmi llm-arena:audit-smoke
```

Result on 2026-10-18: the process ran as uid 10001, migrations applied,
`/health/live` and `/docs` returned 200 with the security headers,
`/api/v1/health/ready` returned 503 (`rabbitmq: failing`, as no broker was started),
and `create-superadmin` plus `POST /api/v1/auth/login` worked (200,
`Cache-Control: no-store`).

## Dynamic scan (OWASP ZAP)

### How to run the ZAP scans

Only against a throwaway instance (own network, scratch database, throwaway
credentials), never against the developer stack. Use unique `audit-zap-`
names and a spare loopback port; remove everything afterwards.

```bash
docker pull ghcr.io/zaproxy/zaproxy:stable
docker build --load -t llm-arena:audit-zap .
docker network create audit-zap-net
docker run -d --name audit-zap-pg --network audit-zap-net \
  -e POSTGRES_USER=zp -e POSTGRES_PASSWORD=<throwaway> -e POSTGRES_DB=zp \
  postgres:17
docker run -d --name audit-zap-mq --network audit-zap-net \
  -e RABBITMQ_DEFAULT_USER=zq -e RABBITMQ_DEFAULT_PASS=<throwaway> \
  rabbitmq:3.13-management
ENVS="-e JWT_SECRET_KEY=<throwaway, 32+ chars> \
  -e ADMIN_SECRET_KEY=<other throwaway, 32+ chars> -e API_DOCS_ENABLED=true \
  -e DATABASE_URL=postgresql+asyncpg://zp:<pw>@audit-zap-pg:5432/zp \
  -e RABBITMQ_URL=amqp://zq:<pw>@audit-zap-mq:5672/ -e API_HOST=0.0.0.0"
docker run --rm --network audit-zap-net $ENVS llm-arena:audit-zap \
  alembic upgrade head
docker run --rm --network audit-zap-net $ENVS \
  -e SUPERADMIN_PASSWORD=<throwaway> llm-arena:audit-zap \
  python cli.py create-superadmin --email zap@example.com
docker run -d --name audit-zap-api --network audit-zap-net $ENVS \
  --security-opt no-new-privileges:true -p 127.0.0.1:18766:8000 \
  llm-arena:audit-zap
# log in (POST /api/v1/auth/login on 127.0.0.1:18766) and keep the
# access_token in $TOKEN; do not print it
mkdir -p reports && chmod 777 reports   # outside the repository
# (":z" relabels the mount on SELinux hosts)
docker run --rm --network audit-zap-net -v $PWD/reports:/zap/wrk:rw,z \
  ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
  -t http://audit-zap-api:8000/docs -m 5 -I -r baseline.html -J baseline.json
docker run --rm --network audit-zap-net -v $PWD/reports:/zap/wrk:rw,z \
  ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
  -t http://audit-zap-api:8000/admin/ -m 3 -I -r admin.html -J admin.json
docker run --rm --network audit-zap-net -v $PWD/reports:/zap/wrk:rw,z \
  ghcr.io/zaproxy/zaproxy:stable zap-api-scan.py \
  -t http://audit-zap-api:8000/openapi.json -f openapi \
  -O http://audit-zap-api:8000 -T 5 -I -r api.html -J api.json \
  -z "-config replacer.full_list(0).description=auth \
  -config replacer.full_list(0).enabled=true \
  -config replacer.full_list(0).matchtype=REQ_HEADER \
  -config replacer.full_list(0).matchstr=Authorization \
  -config replacer.full_list(0).regex=false \
  -config replacer.full_list(0).replacement=Bearer\ $TOKEN"
docker rm -f audit-zap-api audit-zap-pg audit-zap-mq
docker network rm audit-zap-net; docker rmi llm-arena:audit-zap
```

The scanner reaches the API by its network name, so the scan never touches
host port 8000. Reports contain no token (checked by searching them for it).

### ZAP results

First run on 2026-10-18 and re-scan on 2026-10-19 after the Content Security
Policy was added. ZAP 2.17.0 (`ghcr.io/zaproxy/zaproxy:stable`), PostgreSQL
17, RabbitMQ 3.13, `/api/v1/health/ready` returned 200.

Scans: passive baseline of `/docs` (5 URLs), `/redoc` (added on the re-scan)
and the admin panel `/admin/` (9 URLs); the OpenAPI-driven active scan of
`/openapi.json` (102 URLs, authenticated as a throwaway superadmin; the
server log shows 114 successful creations, 236 `200` and no `500`, and no
`500` on the re-scan); and an authenticated panel pass: sign in with
`POST /admin/api/sign-in` (`{"username": <email>, "password": ...}`, answers
200 and sets the `admin_session_id` cookie) and scan `/admin/` again with a
ZAP replacer that sends that cookie (`-config
replacer.full_list(0).matchstr=Cookie`, same form as the `Authorization`
replacer above). The panel is a single-page app and ZAP's spider does not run
JavaScript, so the cookie pass sees the same 9 URLs as the anonymous one. The
authenticated panel API was therefore checked by hand: `GET /admin/api/me`,
`/admin/api/configuration`, `/admin/api/list/User`,
`/admin/api/list/Organization` and a 404 `retrieve` all returned the four
security headers, the strict CSP and `Cache-Control: no-store`, and
`/admin/api/me` without the cookie returned 401. The CSP sources were
compared with the served HTML: every script, stylesheet, image, font and
worker origin used by `/docs`, `/redoc` and `/admin/` is allowed by that
page's policy. A real browser render was not done. The earlier reports were
kept outside the repository, and contain no token (checked by searching them
for it).

No High-risk alert in either run. Medium alerts, before and after:

| Scan | 2026-10-18 | 2026-10-19 |
| ---- | ---------- | ---------- |
| `/docs` baseline | 10038, 90003 | 90003 only |
| `/redoc` baseline | not run | 90003 only |
| `/admin/` baseline | 10038 | 10055 only |
| `/admin/` with session cookie | not run | same as anonymous |
| API scan (authenticated) | no Medium or High | no Medium or High (only 90004 Low and informational) |

Outcomes (details in the register): 10038 (CSP header not set) fixed;
90003 (Sub Resource Integrity attribute missing) accepted; 10055 (CSP
`style-src 'unsafe-inline'`) accepted.

Low and informational alerts, unchanged between runs and accepted: Cross-Domain
JavaScript Source File Inclusion (10017, the CDN scripts on `/docs` and
`/redoc`); Cross-Origin-Embedder, -Opener and -Resource-Policy missing
(90004); Permissions Policy missing (10063); Timestamp Disclosure and
Suspicious Comments (10096, 10027: false positives in the panel's bundled
`index.min.js`); and informational Modern Web Application (10109), Storable
and Cacheable Content (10049), Authentication Request Identified (10111) and
Client Error response code (100000, expected 4xx for invalid fuzzed input).
No wildcard or missing-directive CSP alert was raised.

## OWASP coverage

Status values: covered, partially covered, gap, not applicable. "Covered"
means the control exists and a test or code pointer shows it, not that the
risk is gone. Every backtick-quoted path and `file::test_name` below is
checked by `tests/security/test_security_doc_pointers.py`, so a renamed or
deleted file or test fails the suite. Gaps are repeated in the findings
register.

### OWASP Top 10 (2021)

| Item | Status | How it is addressed | Evidence |
| ---- | ------ | ------------------- | -------- |
| A01 Broken Access Control | covered | Every route except health, login, refresh, logout and validate needs a bearer token. The principal is loaded from the database on each request, and every query is scoped by owner or organization. Out-of-scope resources answer 404 exactly like missing ones. | `components/core/access.py`, `api/v1/utils.py`, `docs/access-control.md`, `tests/core/test_route_coverage.py::test_every_route_requires_a_token_unless_public`, `tests/core/test_isolation.py::test_other_staff_and_other_organization_see_404_like_unknown`, `tests/core/test_access.py::test_staff_scoped_to_owner` |
| A02 Cryptographic Failures | partially covered | Passwords use Argon2id; JWTs are HS256 with a required secret of at least 32 characters that rejects the `.env.example` placeholder; secrets are `SecretStr` and masked. The app does not terminate TLS (use a reverse proxy), HSTS is opt-in, database and broker connections are not forced to TLS, and data is not encrypted at rest. | `components/auth/security.py`, `components/core/config.py`, `tests/auth/test_security.py::test_hash_is_argon2id_salted_and_hides_password`, `tests/test_config.py::test_jwt_secret_too_short_is_rejected_without_echo`, `tests/core/test_middleware.py::test_hsts_only_when_enabled` |
| A03 Injection | covered | All queries go through SQLAlchemy with bound parameters; the only interpolated SQL is a migration over a constant table list (Bandit B608, justified). Dataset text is inserted into prompts literally, never evaluated. CSV exports neutralize formula prefixes. Model output is stored and never executed. Prompt injection into the model itself is inherent to the product and is contained by treating all output as data. | `migrations/versions/20261018_1200_0006_resource_ownership.py`, `components/experiments/utils.py`, `components/metrics/export.py`, `tests/security/test_prompt_rendering.py::test_hostile_input_is_inserted_unchanged`, `tests/security/test_export_encoding.py::test_every_formula_prefix_is_neutralized`, `tests/security/test_pipeline.py::test_runner_stores_hostile_output_verbatim`, `tests/admin/test_panel_hardening.py::test_malformed_filters_are_controlled_and_not_logged` |
| A04 Insecure Design | partially covered | Tenant isolation, bounded request sizes, bounded concurrency, idempotent task execution and stored-response limits are designed in. There are no per-tenant quotas or spend caps, and no request rate limiting. | `components/experiments/schemas.py`, `components/experiments/runner.py`, `tests/experiments/test_runner.py::test_concurrency_limit_is_never_exceeded`, `tests/experiments/test_runner.py::test_duplicate_delivery_makes_one_request`, `tests/experiments/test_response_bounds.py::test_over_limit_text_is_cut_to_the_limit_with_a_visible_marker` |
| A05 Security Misconfiguration | partially covered | Security headers and a request body limit wrap the whole app, the default bind is loopback, the container runs as a non-root user with `no-new-privileges`, and the admin panel is off without its secret. Swagger UI and ReDoc are on by default (switch: `API_DOCS_ENABLED`), HSTS is off by default, and base images are tagged, not pinned by digest. | `api/middleware.py`, `Dockerfile`, `docker-compose.yml`, `tests/core/test_middleware.py::test_headers_on_success_and_error_responses`, `tests/test_config.py::test_api_host_defaults_to_loopback`, `tests/core/test_openapi.py::test_docs_can_be_turned_off`, `tests/admin/test_mount.py::test_app_without_secret_answers_404_for_admin` |
| A06 Vulnerable and Outdated Components | partially covered | Dependencies are locked and `poetry run pip-audit` reported no known vulnerabilities on 2026-10-18; Bandit runs as a pre-commit hook. The audit is a manual command (no CI or scheduled run), and `pip install poetry` in the Dockerfile and the base image are not pinned. | `poetry.lock`, `pyproject.toml`, `.pre-commit-config.yaml`, `tests/security/test_nosec_reasons.py` |
| A07 Identification and Authentication Failures | partially covered | Argon2id with rehash, 12 to 128 character password policy, uniform login failures with a dummy verification for unknown users, short-lived access tokens, rotating refresh tokens with reuse detection, and session revocation on deactivation or password change. There is no login throttling or lockout, and no MFA. | `components/auth/service.py`, `docs/authentication.md`, `tests/auth/test_api.py::test_login_failures_have_identical_body`, `tests/auth/test_service.py::test_login_failures_are_uniform`, `tests/auth/test_service.py::test_refresh_reuse_revokes_family`, `tests/auth/test_users_api.py::test_password_change_ends_sessions_and_swaps_password` |
| A08 Software and Data Integrity Failures | partially covered | No unsafe deserialization (pickle, yaml load, eval, exec are absent, and Bandit would flag them), JWT algorithm pinned so `alg: none` and algorithm confusion fail, locked dependencies with hashes, schema changes only through Alembic. There is no CI pipeline, image signing or SBOM. | `components/auth/security.py`, `poetry.lock`, `tests/auth/test_security.py::test_algorithm_none_is_rejected`, `tests/auth/test_security.py::test_other_hmac_algorithm_is_rejected` |
| A09 Security Logging and Monitoring Failures | gap | Logs carry ids and statuses and tests assert that secrets and hostile content never reach them. Failed logins, refresh-token reuse, permission denials and admin or user-management actions are not logged, there is no audit trail, and no alerting or central log pipeline. | `components/auth/service.py`, `admin/__init__.py`, `tests/auth/test_logging.py::test_auth_flows_do_not_log_secrets`, `tests/core/test_isolation.py::test_nothing_sensitive_reaches_debug_logs`, `tests/security/test_pipeline.py::test_no_log_record_contains_hostile_text` |
| A10 Server-Side Request Forgery | not applicable | The API never fetches a user-supplied URL. Provider endpoints are the SDK defaults, with only the API key configurable; there are no URL fields in the schemas or settings. SDKs read their own environment variables, which only the operator controls. The Kan helper script validates its URL scheme. | `components/third_party/llm/providers/openai.py`, `components/third_party/llm/providers/anthropic.py`, `components/core/config.py`, `tests/security/test_kan_client.py::test_non_http_scheme_is_rejected` |

### OWASP API Security Top 10 (2023)

| Item | Status | How it is addressed | Evidence |
| ---- | ------ | ------------------- | -------- |
| API1 Broken Object Level Authorization | covered | Object identifiers are UUIDs and every repository query adds the owner or organization clause; cross-tenant ids answer 404, also for referenced datasets in experiment creation. | `components/core/access.py`, `tests/core/test_isolation.py::test_other_staff_and_other_organization_see_404_like_unknown`, `tests/datasets/test_api.py::test_out_of_scope_is_404_identical_to_missing`, `tests/experiments/test_api.py::test_another_users_dataset_is_404_and_nothing_is_stored` |
| API2 Broken Authentication | partially covered | Same controls and same gap as A07: strong hashing, token pinning and rotation are tested, but login and refresh are not throttled. | `components/auth/service.py`, `api/v1/endpoints/auth.py`, `tests/auth/test_api.py::test_alg_none_is_401`, `tests/auth/test_api.py::test_refresh_and_reuse`, `docs/authentication.md` |
| API3 Broken Object Property Level Authorization | covered | Request schemas name the accepted fields, so forged ownership fields are ignored; roles cannot be escalated to superadmin over HTTP; response schemas do not include password hashes. | `components/users/schemas.py`, `components/datasets/schemas.py`, `tests/core/test_isolation.py::test_dataset_creation_ignores_forged_ownership_fields`, `tests/auth/test_users_api.py::test_cannot_set_superadmin_role`, `tests/auth/test_users_api.py::test_nobody_edits_superadmin` |
| API4 Unrestricted Resource Consumption | partially covered | Body size limit, page size at most 100, field length caps, at most 10 targets and 5 attempts per experiment, request timeout caps, bounded provider and password-hash concurrency, and a stored response cap. There is no rate limiting and no per-tenant quota. | `api/middleware.py`, `components/experiments/schemas.py`, `components/auth/security.py`, `tests/core/test_middleware.py::test_declared_length_over_limit_is_rejected_early`, `tests/auth/test_users_api.py::test_pagination`, `tests/auth/test_security.py::test_password_hashing_concurrency_is_bounded` |
| API5 Broken Function Level Authorization | covered | Role checks per operation (staff, admin, superadmin) with an explicit matrix over every dataset, experiment, evaluation, metrics and export route. | `api/v1/endpoints/users.py`, `api/v1/endpoints/organizations.py`, `tests/auth/test_users_api.py::test_staff_cannot_create`, `tests/auth/test_organizations_api.py::test_forbidden_for_non_superadmin`, `tests/core/test_isolation.py::test_experiment_creation_matrix_and_forged_fields` |
| API6 Unrestricted Access to Sensitive Business Flows | partially covered | The costly flow is starting experiments, which spend provider budget. It needs authentication, is owner-scoped and bounded per experiment. Nothing limits how many experiments a tenant starts or the total spend. | `api/v1/endpoints/experiments.py`, `components/experiments/schemas.py`, `tests/core/test_isolation.py::test_creation_without_token_is_401` |
| API7 Server Side Request Forgery | not applicable | Same as A10: no user-controlled URL is requested. | `components/third_party/llm/providers/openai.py`, `components/core/config.py` |
| API8 Security Misconfiguration | partially covered | Same as A05: headers, body limit, `no-store` for credentialed requests, loopback default and no default CORS. API docs are public by default; TLS is left to the proxy. | `api/middleware.py`, `api/app.py`, `tests/core/test_middleware.py::test_no_store_rules`, `tests/core/test_middleware.py::test_documentation_still_loads`, `tests/core/test_openapi.py::test_docs_are_served_by_default` |
| API9 Improper Inventory Management | covered | One API version (`/api/v1`) from one code base, OpenAPI generated from the routes, and tests that fail when a route is missing from the document, a public route is unlisted or the allowlist is stale. The admin panel is kept out of the document. | `api/v1/router.py`, `tests/core/test_route_coverage.py::test_route_walk_matches_the_openapi_document`, `tests/core/test_route_coverage.py::test_public_allowlist_has_no_stale_entries`, `tests/core/test_openapi.py::test_admin_panel_routes_are_not_documented` |
| API10 Unsafe Consumption of APIs | covered | Provider responses are normalized in adapters, treated as untrusted data, cut to a stored limit, and provider error text is not stored. The provider calls use the SDK's default TLS verification. | `components/third_party/llm/providers/base.py`, `components/experiments/runner.py`, `tests/security/test_pipeline.py::test_provider_error_text_is_not_stored`, `tests/security/test_evaluators.py::test_huge_and_deeply_nested_responses_are_bounded`, `tests/experiments/test_response_bounds.py::test_short_and_exact_limit_text_is_untouched` |

Summary: Top 10 has 2 covered, 6 partially covered, 1 gap and 1 not
applicable; API Top 10 has 5 covered, 4 partially covered, 0 gaps and 1 not
applicable.

## Findings register

| Source | Severity | Location | Outcome |
| ------ | -------- | -------- | ------- |
| Bandit B104 | Medium | `components/core/config.py` (`api_host`) | Fixed: default is `127.0.0.1`; the Compose `api` service sets `API_HOST=0.0.0.0`; documented in `.env.example` and README; `tests/test_config.py::test_api_host_defaults_to_loopback` |
| Bandit B608 | Medium | `migrations/versions/20261018_1200_0006_resource_ownership.py` | Justified: the interpolated names come from the module-level `TABLES` constant, never user input (`# nosec B608` with reason) |
| Bandit B310 | Medium | `scripts/kan.py` (`urlopen`) | Fixed and justified: `KanClient.__init__` rejects non-http(s) URLs (`tests/security/test_kan_client.py`), then `# nosec B310` with reason |
| Bandit B101 | Low | `admin/__init__.py` (`_export_environment`) | Fixed: explicit `RuntimeError` when `ADMIN_SECRET_KEY` is unset (`tests/admin/test_mount.py::test_export_environment_requires_the_admin_secret`) |
| Bandit B101 | Low | `components/experiments/runner.py` (`_claim`) | Fixed: explicit `RuntimeError` when a claimed result has no `started_at` (`tests/experiments/test_runner.py::test_claim_without_started_at_is_an_internal_error`) |
| Bandit B311 | Low | `components/experiments/runner.py` (jitter) | Justified: non-cryptographic retry jitter (`# nosec B311` with reason) |
| Bandit B105 | Low | `cli.py` (`PASSWORD_ENV`) | Justified: the constant is the name of an environment variable, not a password (`# nosec B105` with reason) |
| Bandit B105 | Low | `components/experiments/utils.py` (`INPUT_TOKEN`) | Justified: prompt template placeholder, not a password (`# nosec B105` with reason) |
| Bandit B105 | Low | `components/experiments/utils.py` (`EXPECTED_OUTPUT_TOKEN`) | Justified: prompt template placeholder, not a password (`# nosec B105` with reason) |
| pip-audit | n/a | all locked dependencies (`poetry run pip-audit`, 2026-10-18) | No findings: the run reported `No known vulnerabilities found`; nothing to upgrade, no package versions changed |
| OWASP A07, API2, API4 | Medium | `components/auth/service.py` (login, refresh) | Follow-up card: no login throttling or lockout (REST and admin panel); mitigated by the documented proxy limit in `docs/authentication.md` and the bounded password-hash concurrency |
| OWASP A09 | Medium | `components/auth/service.py`, `admin/__init__.py` | Follow-up card: failed logins, token reuse, permission denials and admin actions are not logged and there is no audit trail |
| OWASP A04, API4, API6 | Medium | `components/experiments/service.py` | Follow-up card: no per-tenant quota or spend cap on experiments |
| OWASP A02, API8 | Low | deployment | Accepted: TLS termination is out of scope for the app; terminate TLS at a reverse proxy and set `HSTS_MAX_AGE_SECONDS` there |
| OWASP A05, A06 | Low | `Dockerfile` | Follow-up card: pin the base image by digest and the Poetry version; run `pip-audit` on a schedule or in CI |
| OWASP A05, API8 | Low | `api/app.py` | Accepted: Swagger UI and ReDoc are on by default for developers; `API_DOCS_ENABLED=false` turns them off (`tests/core/test_openapi.py::test_docs_can_be_turned_off`) |
| OWASP A08 | Low | build and release | Accepted: no CI, image signing or SBOM in this project |
| OWASP A07 | Low | authentication | Accepted: no MFA; passwords and bearer tokens only |
| ZAP 10038 | Medium | `GET /docs` (Swagger UI), `GET /admin/` (panel) | Fixed (verified by re-scan 2026-10-19; the panel now only raises 10055, next row): `Content-Security-Policy` on every response, page policies with hashed inline scripts for `/docs`, `/redoc` and `/admin` (`tests/core/test_middleware.py::test_documentation_pages_policy_covers_their_sources`, `tests/core/test_middleware.py::test_admin_panel_policy_covers_its_sources`, `tests/core/test_middleware.py::test_non_html_responses_get_the_strict_policy`) |
| ZAP 10055 | Medium | `GET /admin/` (`style-src 'unsafe-inline'`) | Accepted: the panel UI needs inline styles, which CSP cannot allow by hash; scripts stay limited to `'self'` and one hashed inline script (`api/middleware.py`). Follow-up: replace or patch the panel UI to drop `'unsafe-inline'` |
| ZAP 90003 / 10017 | Medium / Low | `GET /docs`, `GET /redoc` (`cdn.jsdelivr.net`, ReDoc also Google Fonts) | Accepted: the CDN script and stylesheet have no `integrity` attribute. Reasons: the docs are a developer convenience that `API_DOCS_ENABLED=false` removes, the CSP limits the page to that one CDN origin, and the version is pinned to the `@5` major (FastAPI's default). Follow-up: pin an exact version with SRI hashes or serve the assets locally |
| ZAP 90004, 10063 | Low | all responses | Accepted: Cross-Origin-*-Policy and Permissions-Policy headers are defence in depth for browser pages; the API returns JSON |
| ZAP 10096, 10027 | Low / Info | `/admin/static/index.min.js` | Accepted: false positives in the third-party panel bundle |
| ZAP 10049, 10109, 10111, 100000 | Info | various | Accepted: informational only |

### Code review (2026-10-19)

The whole code base was reviewed read-only in five slices (1 core, auth,
users, organizations and access; 2 datasets, experiments and evaluation; 3
metrics, exports and LLM providers; 4 API, admin, CLI and tasks; 5 tests,
tooling, Docker and docs) against the checklist in the change design.

Scope decision: the project is an MVP, so the High findings and the security
findings were fixed in this change (F1 to F6 below). Every other finding is
tracked as a tech-debt card on the Kan board, one card per theme, written
here as "Kan tech-debt: <theme>". Items marked Medium or Low are the reviewer's
severity; none of the tracked ones is a known exploitable weakness beyond what
is stated.

| Source | Severity | Location | Outcome |
| ------ | -------- | -------- | ------- |
| F1 Code review slice 2 | High | `components/experiments/service.py`, `components/experiments/utils.py`: rendered prompt size unbounded (a template repeating `{input}` over large cases, up to 10000 results, could exhaust memory and storage with one request) | Fixed: `rendered_length` computes sizes before any prompt is built; a prompt over 200000 characters, or more than 100000000 in total, is rejected with `422` and nothing stored (`tests/experiments/test_prompt_size.py::test_amplification_is_rejected_before_rendering`, `tests/experiments/test_prompt_size.py::test_boundary_exact_limit_passes_one_over_fails`, `tests/experiments/test_prompt_size.py::test_normal_prompts_are_unaffected`, `tests/experiments/test_service.py::test_oversized_rendered_prompt_is_rejected_and_nothing_stored`) |
| F2 Code review slice 5 | High | `conftest.py` (`engine`), `tests/test_database.py`: tests dropped and created tables on whatever `DATABASE_URL` held, so exporting the dev `.env` wiped the dev database | Fixed: every test that builds an engine uses `checked_database_url`, which fails unless the database name contains `test` (`tests/test_database_guard.py::test_checked_url_fails_the_test_for_a_dev_database`); documented in `docs/testing.md` |
| F3 Code review slice 5 | Medium (security) | `.dockerignore`: `.env.kan` (Kan API key), `.claude`, tests, scripts and docs entered the image build context | Fixed: `.env.*` (except `.env.example`), `.claude`, `tests`, `scripts`, `docs`, `docker-compose*.yml` and others are excluded; verified by building the image and listing `/app` (no `.env.kan`, `.claude`, `tests`), and `import api.app` in it |
| F4 Code review slice 4 | Medium (security) | `api/middleware.py`: an unhandled exception answered 500 from Starlette's outer error handler, without security headers, CSP or `no-store` | Fixed: the middleware answers a generic `{"detail": "Internal Server Error"}` itself (an app-level exception handler would also sit outside it) and logs the traceback server-side (`tests/core/test_middleware.py::test_unhandled_error_is_a_generic_500_with_the_hardening_headers`, `tests/core/test_middleware.py::test_error_after_the_response_started_is_not_masked`) |
| F5 Code review slice 4 | Medium (security) | `api/middleware.py`: the response paths assumed `headers` and `body` keys that ASGI allows an app to omit | Fixed: defaults are used (`tests/core/test_middleware.py::test_response_messages_may_omit_headers_and_body`) |
| F6 Code review slice 3 | Medium (security) | `components/metrics/service.py`: `pricing_error` returned the server filesystem path of `PRICING_FILE` to any authenticated caller | Fixed: the API gives only the reason, the path is logged server-side (`tests/metrics/test_pricing.py::test_service_pricing_error_hides_the_path_and_logs_it`, `tests/metrics/test_service.py::test_invalid_pricing_keeps_other_metrics`) |
| Code review slices 1, 4 (Medium s1 #2 to #4; Low s1 #9 to #13, slice 4 L11) | Medium / Low | `components/auth`: access tokens outlive logout and password change until they expire, refresh tokens are never purged, deactivation can race an in-flight refresh, cancelled login leaves a hashing thread running, import-time dummy hash, no clock-skew leeway, double-submit refresh logs the user out, `/auth/validate` token-state reasons | Tech debt: Kan tech-debt: Auth hardening |
| Code review slice 1 (Medium s1 #1; Low #5 to #8, #20, #21) | Medium / Low | `components/users`, `components/organizations`: `session.rollback()` after `IntegrityError` rolls back the whole request transaction (use a savepoint), dead superadmin branch, email enumeration via 409, peer-admin takeover, lost update in `update_user`, auth/organizations coupling, exception naming | Tech debt: Kan tech-debt: Users/Organizations services |
| Code review slice 1 (Low #14 to #19, #22) | Low | `components/core`: `commit()` drops all but the first `on_commit` exception, callbacks survive a failed commit, dead `BaseRepository.list`, docs on by default, unvalidated settings, weak JWT secret check, `Any` in `access.py` | Tech debt: Kan tech-debt: Core cleanup/config |
| Code review slice 2 (Medium #3 to #6; Low #8 to #14) | Medium / Low | `components/experiments/runner.py`, `models.py`: claim taken before the provider semaphore (waiting burns the lease), cancellation leaves a result running until lease expiry, unprotected fallback write and lone surrogates, `Retry-After` capped at 30 seconds with no jitter at the cap, retry budget reset on reclaim, bad provider config leaves a result pending, duplicate delivery silently dropped, stale comment, misleading log field, experiments/evaluation coupling, `rollback()` outside the core helper | Tech debt: Kan tech-debt: Experiment runner robustness |
| Code review slices 2, 3 (Medium s2 #2, s3 #5) | Medium | `components/experiments/runner.py`, `components/metrics`: truncated responses are evaluated as if complete, and `response_truncated` is not exposed in the per-case results | Tech debt: Kan tech-debt: Truncation visibility |
| Code review slice 2 (Medium #7) | Medium | `components/datasets`: no cap on cases per dataset, no depth, size or key-count bound on metadata | Tech debt: Kan tech-debt: Dataset limits |
| Code review slice 3 (Medium #1, #3, #4; Low #13 to #16) | Medium / Low | `components/metrics`: no index for the export ordering (quadratic export), a mid-stream export failure silently truncates, export header snapshot versus streamed rows, service cleanups, CSV neutralization edge cases (leading whitespace, `\|`), prompts included in JSON export | Tech debt: Kan tech-debt: Metrics/export |
| Code review slice 3 (Medium #6; Low #7 to #12) | Medium / Low | `components/third_party/llm/providers`: `retry_after` accepts `inf`, `client or AsyncX(...)` against CLAUDE.md, connection errors mapped to unknown, duplicated helpers, Anthropic cache tokens, OpenAI reasoning-model empty content, string concatenation nit | Tech debt: Kan tech-debt: LLM providers |
| Code review slice 4 (Low L1 to L6, L8 to L11) | Low | `api`, `cli.py`, `admin`: middleware test gaps, `connection: close` on 413 under HTTP/2, CSP allows the whole jsDelivr origin, readiness and LLM service bypass the settings dependency, duplicated pagination helpers, no upper bound on `offset`, broker never shut down, `settings or get_settings()`, CLI engine not disposed, no Permissions-Policy, COOP or CORP, no trusted-host setting | Tech debt: Kan tech-debt: API/CLI/middleware cleanups |
| Code review slice 4 (Medium L7) | Medium | login, refresh, validate, admin sign-in: no throttling or lockout; also no security event log (OWASP A09) and no per-tenant quota (A04, API6) | Tech debt: Kan tech-debt: Login rate limiting (same follow-up as the OWASP rows above) |
| Code review slice 5 (Medium M3 to M5; Low L1 to L3, L9) | Medium / Low | `tests`, `conftest.py`, `.pre-commit-config.yaml`: tests not hermetic against a developer `.env`, integration marker applied by hand, migration fixtures imported across test modules, weak or timing-dependent tests, hook order | Tech debt: Kan tech-debt: Test infrastructure |
| Code review slice 5 (Medium M2, M6; Low L4 to L8) | Medium / Low | `docker-compose.yml`, `Dockerfile`, `migrations`, docs: Compose passes only three variables to the containers (provider keys, `PRICING_FILE`, `ADMIN_SECRET_KEY` and `LLM_MAX_CONCURRENCY` never reach them, a functional bug in the documented flow and the priority item; the HTTP hardening and stored-response settings are passed since this change), no named PostgreSQL volume, missing foreign key indexes, thin migration tests, docs lag, loose dependency constraints, unpinned images | Tech debt: Kan tech-debt: Compose, Docker, migrations, docs (priority) |

Checked and found no issue: JWT validation (algorithm, expiry, issuer, type),
refresh rotation atomicity, login uniformity, Argon2 parameters, role
escalation paths, authorization coverage of every route
(`tests/core/test_route_coverage.py`), 404 hiding, admin scoping and
read-only mode, transaction scope (no transaction across provider calls),
claim and lease idempotency, retry of failed cases only, secrets and content
not logged, provider isolation, exact `Decimal` costs, the migration chain
0001 to 0007 and the safety of 0007, `.env.example` against the settings,
that documented commands and environment names exist, and a single
`conftest.py`.

## Accepted risks

| Risk | Reason | Owner | Review date |
| ---- | ------ | ----- | ----------- |
| No login throttling | Documented; put a limiting proxy in front of `/auth/login` and `/auth/refresh` | maintainers | before an internet-facing deployment |
| No TLS in the app | A reverse proxy terminates TLS | maintainers | before an internet-facing deployment |
| Docs on by default | Developer convenience; switchable with `API_DOCS_ENABLED` | maintainers | before an internet-facing deployment |
| Swagger UI and ReDoc loaded from a CDN without SRI (ZAP 90003) | Docs can be disabled; CSP limits the page to the CDN origin; version pinned to the `@5` major | maintainers | before an internet-facing deployment |
| Admin panel CSP keeps `style-src 'unsafe-inline'` (ZAP 10055) | The panel UI sets inline styles that cannot be hashed; scripts stay limited to `'self'` and one hashed inline script | maintainers | before an internet-facing deployment |
| Server logs may contain request-derived text (the 500 path logs the traceback; pydantic and SQL exception messages can embed input) | Responses carry no detail; logs must be access-controlled and treated as sensitive | maintainers | before an internet-facing deployment |
| No MFA, no image signing, no CI or SBOM | Out of scope for this project stage | maintainers | next major release |

## Known gaps and follow-ups

Open work is tracked on the Kan board as tech-debt cards, one per theme:

- Login rate limiting, a security event log and audit trail, and per-tenant
  quotas or spend caps (OWASP A04, A07, A09, API2, API4, API6).
- Auth hardening, Users/Organizations services, Core cleanup/config,
  Experiment runner robustness, Truncation visibility, Dataset limits,
  Metrics/export, LLM providers, API/CLI/middleware cleanups and Test
  infrastructure (the code review rows above).
- Compose, Docker, migrations, docs (priority): Compose does not pass the
  other settings (provider keys, `PRICING_FILE`, `ADMIN_SECRET_KEY`,
  `LLM_MAX_CONCURRENCY`) to the containers, and the base images and the Poetry version are
  not pinned by digest.
- Running `pip-audit` on a schedule or in CI, pinning the docs CDN assets with
  SRI or serving them locally, and replacing or patching the panel UI to drop
  `style-src 'unsafe-inline'`.
- Not verified: the CSP in a real browser, and any deployment behind a
  reverse proxy.

## Untrusted content and future LLM-as-a-judge

Dataset text, model output and provider errors are untrusted. The shared
hostile corpus in `tests/security/helpers.py` (template syntax, placeholder
smuggling, prompt injection, formula prefixes, huge and deeply nested values)
is run against every sink: prompt rendering, the runner, the API, JSON and CSV
export, the admin panel, logs and the evaluators. Any future LLM-as-a-judge
feature must reuse that corpus: a judge receives model output as input, so it
is a new sink, and its prompt rendering, stored verdicts, logs and exports
need the same hostile-input tests before it lands. A judge's own output is
untrusted too and must be stored and exported as data, never executed.
