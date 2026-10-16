# Proposal

## Why

Kan card LLM-3 (stage 2 of the MVP plan). Experiments must send the same prompt to several models, but the platform has no way to call an LLM. A shared provider interface with a normalized result keeps provider-specific SDK behavior out of business logic and is the input for experiment execution, evaluation and metrics.

## What Changes

- Add a provider interface that sends one single-turn request to a model and returns a normalized result: text, token usage, measured latency, provider request id and finish reason.
- Add OpenAI and Anthropic adapters implementing it.
- Add an `LLMService` that selects the provider and model from the caller's run configuration, with no global singleton.
- Normalize provider failures (timeout, rate limit, authentication, invalid request, server error, malformed response) into one error type with a retryable flag.
- Record metrics a provider does not report as null, never as invented values.
- Add optional `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` settings, masked in output.
- Add the `openai` and `anthropic` SDK dependencies (both already named in the project stack).
- Tests use mocked SDK responses; real-provider tests are separate and opt-in.

## Capabilities

### New Capabilities
- `llm-provider-adapters`: sending a model request through a provider-independent interface and receiving a normalized response or a normalized error.

### Modified Capabilities

## Impact

- New package `components/third_party/llm/` (providers, schemas, service) and tests under `tests/llm/`.
- `components/core/config.py` and `.env.example` gain two optional API-key settings.
- `pyproject.toml` and `poetry.lock` gain `openai` and `anthropic`.
- No database changes and no API endpoints; existing code is unchanged.

## Out of Scope

- Retries, concurrency limits and rate-limit backoff (experiment execution owns the retry policy; the adapter only reports whether an error is retryable).
- Cost calculation and pricing versions (metrics stage).
- Structured output, tool calling, streaming and multi-turn conversations.
- Persisting requests or responses; endpoints that expose providers.
- Providers other than OpenAI and Anthropic (for example Ollama).
