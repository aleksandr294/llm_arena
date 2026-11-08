# Design

## Context

`SettingsSchema` validates only the JWT and admin secrets. `commit()` in
`components/core/database.py` awaits every `on_commit` callback but raises
only the first failure, and callbacks registered before a failed
`session.commit()` stay in `session.info`. `SecurityMiddleware`
(`api/middleware.py`) is pure ASGI with fixed header sets and CSP strings.
`api/v1/endpoints/*` each define `Limit`, `Offset` and `_not_found`;
`get_llm_service` in `experiments.py` builds a new `LLMService` (and with it
new SDK clients that are never closed) per request. `taskiq_app` keeps a
module-level `asyncio.Lock` and never shuts the broker down. See proposal.md for
motivation.

Branch `feat/tech-debt-core-api-cleanups`, worktree
`../llm-arena-tech-debt-core`. Shares files with other changes: `config.py`
(auth adds settings), `database.py` (the pipeline change *adds* a `rollback`
helper next to `commit()`), `datasets` untouched elsewhere. Conflicts are
additive; no migration.

## Goals / Non-Goals

**Goals:**
- Fail fast on unusable configuration; honest commit semantics; tighter
  headers; no leaked clients; bounded dataset content.

**Non-Goals:**
- No `TrustedHost` middleware (deployment-specific): documented in
  `docs/security.md` as an operator concern behind a proxy.
- No SRI for CDN assets (accepted risk, recorded in the findings register)
  and no local Swagger bundle: docs are off by default instead.
- No new rate limiting (auth change) and no per-IP logic here.

## Decisions

1. **Settings validation** with pydantic constraints/validators in
   `config.py`: `health_check_timeout_seconds` `gt=0`; `jwt_access_ttl_seconds`
   `le=86400`; `jwt_refresh_ttl_seconds` `le=7_776_000`; `database_url` must
   start with `postgresql+asyncpg://`; `rabbitmq_url` with `amqp://` or
   `amqps://`; one shared `_require_entropy(value, name)` for both secrets:
   at least 12 distinct characters. Messages name the setting only. Existing
   test fixtures (`TEST_JWT_SECRET`) already satisfy this. Alternative: real
   entropy estimation, rejected as false precision; the 12-distinct rule only
   catches obviously weak values and is documented as such.
2. **Docs off by default:** `api_docs_enabled: bool = False`; `.env.example`
   and the CI/sqlmap scripts that need docs already set `true`
   explicitly. README and `docs/api-documentation.md` say so.
3. **`commit()`:** wrap `session.commit()` in `try/except BaseException`
   that pops the callbacks, then re-raises; collect callback errors, raise the
   first, `logger.error` the others with the exception class name only
   (messages may carry content). `session_scope` keeps its own cleanup.
4. **Remove `BaseRepository.list`** and the two overrides that disable it
   (`DatasetRepository`, `ExperimentRepository`) after checking nothing calls
   it; the spec text ("create, read, update, delete") is unaffected.
5. **`access.py` typing:** a `Protocol` `OwnedModel` with `owner_id` and
   `organization_id` as `InstrumentedAttribute[...]`, replacing `Any` and the
   `cast`s. If mypy cannot express it for the models, fall back to a
   `TypeVar` bound to `Base` and keep one targeted `cast`; the task states the
   mypy-clean outcome as the acceptance criterion.
6. **Middleware:** CSP sources for Swagger UI become
   `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/`, ReDoc
   `https://cdn.jsdelivr.net/npm/redoc@2/` (the exact paths FastAPI emits;
   the existing test that checks every script/stylesheet origin is extended to
   check full URLs). `Connection: close` only when
   `scope["http_version"]` is `1.0`/`1.1`. New headers in `SECURITY_HEADERS`:
   `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()`,
   `Cross-Origin-Opener-Policy: same-origin`,
   `Cross-Origin-Resource-Policy: same-origin`. Test gaps closed: streaming
   response plus over-limit body, an app that swallows the receive error,
   and `Cache-Control` set exactly once. A test pins fastadmin's session
   cookie name to `ADMIN_SESSION_COOKIE`.
7. **Lifecycle in `create_app` (entry point):** a FastAPI `lifespan` that on
   shutdown calls a new `shutdown_broker()` in `taskiq_app` (no-op when never
   started) and disposes the engine. The broker lock moves into a small
   `_BrokerState` holding the lock created lazily for the running loop (same
   pattern as the Argon2 semaphore in auth), so a second event loop (tests,
   reload) does not reuse a lock bound to the first. `settings = settings if
   settings is not None else get_settings()`.
8. **One `LLMService` per app:** built in `create_app` from the settings and
   stored on `app.state`; `get_llm_service(request)` returns it. It keeps the
   adapters and their HTTP clients for the process lifetime; they are closed in
   the lifespan shutdown (new `LLMService.aclose()` closing cached adapter
   clients). `ready` and `get_llm_service` depend on `get_settings_dependency`
   so tests can override one place.
9. **Shared helpers:** `Limit`, `Offset` (`le=1_000_000`) and
   `not_found(detail)` move to `api/v1/utils.py`; endpoint modules import them.
   The two `get_enqueue` functions are not duplicates (different task ports)
   and stay. The evaluator filter becomes
   `Query(max_length=EVALUATOR_ID_MAX)` using a named constant extracted from the existing 64-character evaluator id constraint in `components/evaluation/schemas.py`.
10. **CLI:** `_create_superadmin` disposes the engine in `finally`; a
    `SQLAlchemyError`/`OSError` becomes `Error: database unavailable` on
    stderr, exit 1, with no URL or traceback (the class name is logged at
    debug level only).
11. **Dataset limits:** setting `max_cases_per_dataset` (default 10_000,
    `ge=1`) passed to `DatasetService` by its constructor through
    `build_dataset_service(session, settings)` (the endpoint dependency
    supplies settings). `add_case` checks `count` after taking the dataset row
    lock (already serialized), raising `DatasetFullError` -> 409. Metadata:
    `MetadataDict` gets a validator beside `reject_nul_deep`
    (`validate_metadata_bounds`: depth <= 5, total keys <= 100,
    `len(json.dumps(...)) <= 16384`), in `components/core/utils.py` since
    it is a reusable value check.

## Risks / Trade-offs

- [Docs off by default surprises developers] -> `.env.example` has `true`,
  README quick start mentions it.
- [Stricter settings break an existing `.env`] -> errors name the setting;
  documented in the PR notes and `.env.example` comments.
- [Cross-Origin-Resource-Policy may block a consumer that embeds API output
  cross-origin] -> the API is not meant to be embedded; revisit if a browser
  client appears.
- [Counting cases on every add] -> `count` is an indexed lookup on
  `dataset_id` (the unique `(dataset_id, position)` constraint covers it).

## Migration Plan

No schema change. Deploy: update `.env` where the new validation or the
docs default matters, restart API and worker.
