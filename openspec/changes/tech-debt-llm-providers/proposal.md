# Proposal

## Why

Two Kan cards concern the provider adapters (`dfrgttplkmxj`,
`xlzbr92mwozm`). The adapters still have review findings: `Retry-After: inf`
is accepted, connection failures are indistinguishable from unknown errors,
`_log`/`_map_error` are duplicated, Anthropic usage ignores cache tokens and
OpenAI reasoning models that run out of tokens lose their usage. Separately,
generation speed is only an approximation because the adapters never
stream, so there is no true time to first token (TTFT). Both live in
`components/third_party/llm`.

## What Changes

- `retry_after` rejects non-finite values (`inf`, `nan`).
- New error kind `connection` (retryable) for `APIConnectionError`.
  **BREAKING** for consumers that match `error_kind == "unknown"` for network
  failures; existing stored rows are not rewritten.
- Shared call logging and SDK error mapping replace the two copies.
- Anthropic input tokens include cache creation and cache read tokens when the
  provider reports them.
- An empty completion that ends with `finish_reason=length` fails as
  `malformed_response` but keeps the finish reason and token usage, which the
  runner stores on the failed result.
- Optional streaming mode (`LLM_MEASURE_TTFT`, default off) in both adapters
  measures time to the first text chunk, returns it on the response and
  exposes the new `model_call_ttft_seconds` metric next to the existing
  approximation.
- Cleanups: `client or AsyncX(...)` becomes an `is None` check, implicit string
  concatenation in `exceptions.py` is removed.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `llm-provider-adapters`: new `connection` kind, usage kept on empty
  responses, cache tokens counted, optional first-token timing.
- `observability`: TTFT metric when streaming is enabled.

## Impact

- Code: `components/third_party/llm/{enums,exceptions,schemas,utils,service}.py`,
  `components/third_party/llm/providers/{base,openai,anthropic}.py`,
  `components/experiments/model_metrics.py`, `components/experiments/runner.py`
  (two small edits: record TTFT, store usage of a failed call),
  `components/core/config.py`, `observability/grafana/dashboards/llm-arena.json`.
- Database: none (TTFT is a metric, not a stored column).
- Settings: `LLM_MEASURE_TTFT` (default false).
- Docs: `docs/observability.md`, `docs/experiments-api.md` (error kinds), `.env.example`.
- No new dependencies (the installed `anthropic` and `openai` SDKs must expose the streaming APIs used; verified in task 3.1).
