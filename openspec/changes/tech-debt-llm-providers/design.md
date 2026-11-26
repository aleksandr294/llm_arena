# Design

## Context

`OpenAIProvider` and `AnthropicProvider` each implement `generate()` with a
single non-streaming SDK call, a private `_log` and a private `_map_error`
that are copies apart from the SDK exception module. `LLMErrorKind` has no
connection kind; `LLMProviderError` carries kind, retryability, request id and
retry-after. The runner records latency and an approximate generation speed
(`output_tokens / latency`); `docs/observability.md` says it is not TTFT. See
proposal.md for motivation.

Branch `feat/tech-debt-llm-providers`, worktree
`../llm-arena-tech-debt-providers`. It shares `runner.py` with
`tech-debt-experiment-pipeline` (two small edits here); whichever merges second
resolves a trivial conflict. No migration.

## Goals / Non-Goals

**Goals:**
- One implementation of logging and error mapping; errors that say what
  happened; no lost usage on empty completions.
- A measured TTFT that does not change the default request path.

**Non-Goals:**
- No streaming to API clients and no partial results; streaming is only an
  internal way to timestamp the first chunk.
- TTFT is not stored per result (no column, no API field): it is an
  operational metric, which avoids a migration and an API contract.
- No new provider, no tool calling or structured output.

## Decisions

1. **`connection` kind.** Added to `LLMErrorKind` and `RETRYABLE_KINDS`; the
   explicit `retryable=True` arguments disappear. Old stored `unknown` rows
   stay as they are. Alternative: keep `unknown` + retryable flag, rejected
   because the error rate by kind could not separate network from bugs.
2. **Shared helpers in `llm/utils.py`:** `log_call(provider, request, outcome,
   started)` and `map_sdk_error(provider, exc, errors)` where `errors` is a
   small frozen dataclass of the four SDK classes (`APITimeoutError`,
   `APIConnectionError`, `APIStatusError`, `APIResponseValidationError`) each
   adapter passes. Provider-specific SDK knowledge stays in the adapters
   (CLAUDE.md); the mapping rules are written once. `retry_after` uses
   `math.isfinite`.
3. **`if client is None`** in both constructors (CLAUDE.md DI rule).
4. **Anthropic usage:** `input_tokens = input + (cache_creation or 0) +
   (cache_read or 0)` using `getattr(..., None)`, so older response shapes
   still work. `total_tokens` stays null (Anthropic reports none).
5. **Empty completion keeps usage.** `LLMProviderError` gets optional
   `finish_reason: str | None` and `usage: LLMUsageSchema | None`; both adapters
   fill them before raising `malformed_response`. The runner's
   `LLMProviderError` branch passes them into `_failure`, which stores
   `finish_reason`, `input_tokens`, `output_tokens` and `total_tokens` (None
   stays None). Messages still carry no content.
6. **Streaming mode is opt-in:** `llm_measure_ttft: bool = False` in
   settings; `LLMService` already holds the settings, so it passes the flag to
   `default_provider_factories(measure_ttft=...)`, which hands it to each
   adapter constructor (adapters never read settings). When on:
   - Anthropic: `client.messages.stream(...)` (same `extra_body`
     temperature), first non-empty `text_stream` item stamps TTFT,
     `get_final_message()` supplies model, stop reason, usage and request id,
     so the rest of the code path is shared with the non-streaming result.
   - OpenAI: `chat.completions.create(stream=True,
     stream_options={"include_usage": True})`; first chunk with non-empty
     `choices[0].delta.content` stamps TTFT; text is the concatenation; the
     last chunk with `usage` gives usage; `finish_reason` comes from the chunk
     that sets it.
   - The whole consumption is wrapped in `asyncio.timeout(timeout_seconds)`
     because SDK timeouts are per-read for streams; expiry maps to `timeout`.
   - Any error mid-stream is mapped by `map_sdk_error`; partial text is
     discarded (the platform's retry policy owns retries).
   Alternative: always stream. Rejected: it changes error and usage behavior
   of every call for a metric most deployments do not read.
7. **Schema:** `LLMResponseSchema.time_to_first_token_ms: float | None =
   None` (`ge=0`), measured with `time.perf_counter()` from the same
   `started` as latency, so TTFT <= latency by construction.
8. **Metric:** `model_call_ttft_seconds` histogram (provider, model) with
   small buckets (0.05 s to 30 s) in `model_metrics.py`; `record_ttft` is a
   no-op for `None`; the runner calls it next to `record_latency`. A TTFT panel
   is added to the starter dashboard; the "approximate speed" wording in
   `docs/observability.md` keeps its caveat for streaming-off.

## Risks / Trade-offs

- [SDK stream APIs differ by version] -> adapter tests use fake streams built
  from the installed SDK types and a version-pinned import check; real-provider
  tests stay separate and opt-in (CLAUDE.md).
- [Streaming usage differs: OpenAI only reports usage with `include_usage`,
  some compatible endpoints omit it] -> usage stays `None` (unknown), never
  invented.
- [`connection` kind changes dashboards that filter on `unknown`] -> noted in
  docs and the changelog line of the PR.

## Migration Plan

No database change. Default behavior is unchanged (`LLM_MEASURE_TTFT=false`).
Roll out by setting the variable on the worker only; roll back by unsetting it.
