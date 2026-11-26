# Proposal

## Why

Three Kan cards collect low-severity findings of the code review in the shared
core, the HTTP layer and the dataset limits (`pyor7ltgb143`, `gba8wdsj9wnt`,
`6wbgk022agsg`): unvalidated settings, a weak-secret check that accepts
low-entropy values, `on_commit` failures lost silently, hardening gaps (CSP
trusting a whole CDN origin, `Connection: close` on HTTP/2, missing
cross-origin headers), a broker that is never shut down, an `LLMService` and its
HTTP clients rebuilt for every request, and datasets whose metadata and size
are unbounded. They touch `components/core`, `api/` and `cli.py` and are
independent of auth, the experiment pipeline and the providers.

## What Changes

- Settings are validated: health timeout `> 0`, JWT TTL ceilings,
  `DATABASE_URL`/`RABBITMQ_URL` scheme checks, and a minimum number of
  distinct characters for the JWT and admin secrets.
- API docs are **off by default** (`API_DOCS_ENABLED=false`). **BREAKING** for
  deployments that relied on the default; `.env.example` keeps `true` for local
  development.
- `commit()` reports every failing `on_commit` callback and discards callbacks
  when the commit itself fails; `BaseRepository.list` (dead, no `ORDER BY`)
  is removed together with the overrides that disabled it.
- `access.py` drops `Any` typing.
- HTTP: CSP pins the jsDelivr paths of Swagger UI and ReDoc, 413 sends
  `Connection: close` only on HTTP/1, new `Permissions-Policy`,
  `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy` headers,
  pagination offset and `evaluator_id` filter are bounded, middleware test gaps
  are closed.
- App lifecycle: one `LLMService` per application, readiness and the LLM
  service use the settings dependency, the broker is shut down with the app,
  `create_app` uses an `is None` default, shared `Limit`/`Offset`/not-found
  helpers.
- CLI: disposes the engine and reports database errors without a traceback.
- Datasets: a maximum number of cases per dataset and bounds for case metadata
  (depth, key count, serialized size).

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `service-runtime`: validated settings, bounded query parameters, clean
  shutdown, CLI error reporting.
- `api-documentation`: documentation off unless enabled.
- `database-persistence`: commit callback semantics.
- `http-hardening`: pinned CSP sources, cross-origin and permission headers,
  `Connection: close` only where valid.
- `dataset-management`: case count limit, metadata bounds.

## Impact

- Code: `components/core/{config,database,repository,access,utils}.py`,
  `components/*/repository.py` (remove `list` override),
  `components/datasets/{schemas,service,exceptions,dependencies}.py`,
  `api/{app,middleware}.py`, `api/v1/{utils,endpoints/*}.py`, `taskiq_app.py`, `cli.py`.
- Settings: new `MAX_CASES_PER_DATASET`; `API_DOCS_ENABLED` default changes.
- No database migration. API: new 409 on a full dataset and 422 on oversized
  metadata (additive limits).
- Docs: `docs/security.md` (findings register, A05 row), `docs/api-documentation.md`,
  `docs/datasets-api.md`, README limitation list, `.env.example`.
- No new dependencies.
