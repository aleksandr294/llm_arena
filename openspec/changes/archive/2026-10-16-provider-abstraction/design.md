# Design

## Context

No LLM code exists yet. `CLAUDE.md` fixes the location (`components/third_party/llm/` with `providers/`, `schemas.py`, `service.py`) and requires provider SDK behavior to stay inside adapters. Settings use `pydantic-settings` with `SecretStr`. See proposal.md for motivation and the spec for behavior.

## Goals / Non-Goals

**Goals:**
- One small interface that both providers implement, with a result shape that never hides missing metrics.
- Business logic and later experiment code depend only on the interface and schemas, never on SDK types.
- Deterministic tests with no network.

**Non-Goals:**
- Retry, backoff, concurrency, cost, persistence, tools, structured output, streaming (see proposal).

## Decisions

- **Layout:** `providers/base.py` (`BaseLLMProvider`, `LLMProviderError`), `providers/openai.py`, `providers/anthropic.py`, plus `schemas.py` and `service.py`. Module names `openai.py` and `anthropic.py` are safe because imports are absolute (`from openai import AsyncOpenAI`).
- **Interface:** `BaseLLMProvider.generate(request: LLMRequestSchema) -> LLMResponseSchema`, async. The request carries `model, prompt, system_prompt, temperature, max_tokens, timeout_seconds`. Single-turn text only; richer inputs can be added later without breaking callers because capabilities are not assumed identical.
- **Response schema:** `text, provider, requested_model, reported_model, finish_reason, provider_request_id, usage (input/output/total tokens, each nullable), latency_ms`. `latency_ms` is measured with `time.perf_counter` around the SDK call, so it is always a measured value; total tokens are taken from the provider when given and otherwise null (never summed silently), keeping measured and derived values distinct.
- **Errors:** adapters raise `LLMProviderError(kind, provider, retryable, request_id, retry_after_seconds)`. `kind` is a lowercase `StrEnum`. Mapping from SDK exceptions lives only in each adapter; retryable = timeout, rate_limit, server_error. Raising (instead of returning an error result) keeps the success type clean; the experiment layer will catch it and persist the failure per case.
- **No singleton / config-driven selection:** `ModelTargetSchema(provider, model)` is part of the caller's run config. `LLMService(settings)` builds adapters lazily per instance from a provider-name registry and caches them on that instance; there is no module-level client. Adapters accept an optional SDK client in the constructor so tests inject mocks.
- **Settings:** `openai_api_key` and `anthropic_api_key` as `SecretStr | None` in `SettingsSchema`; `.env.example` lists them (an existing test requires every setting there). A missing key raises `ProviderNotConfiguredError` naming the setting.
- **Required `max_tokens` (Anthropic):** the Messages API cannot omit it, so the Anthropic adapter falls back to 4096 and the response carries `effective_max_tokens` (the value actually sent, or null when none was sent) so the run configuration stays explainable. This is the only invented default and is an explicit exception to "never invent values". Decided with the user during implementation.
- **Timeouts:** the per-request timeout is passed to the SDK call, and SDK-level automatic retries are disabled (`max_retries=0`) so the platform's retry policy is the only one.
- **Dependencies:** `openai` and `anthropic` official SDKs, already named in the project stack; no wrapper libraries.
- **Logging:** adapters log only provider, model, outcome kind and latency; never keys, prompts or responses.
- **Tests:** unit tests with mocked SDK clients for each adapter (success, missing usage, each error kind, malformed response) and the service (selection, unsupported provider, missing key). Real-provider tests live in `tests/llm/real/`, marked `real_provider`, and are skipped unless `RUN_REAL_PROVIDER_TESTS=1` and the keys are set.

## Risks / Trade-offs

- [SDK exception types change between versions] → pin a compatible major range and cover each mapped exception in adapter tests.
- [Providers report finish reasons and usage differently] → the schema keeps provider values as strings/nullable and does not translate semantics; only the field names are unified.
- [Single-turn text may be too narrow for later evaluators] → additive fields later; out of scope now.
- [Models that reject a parameter (for example temperature)] → surfaced as `invalid_request`; per-model capability tables are not built now.
