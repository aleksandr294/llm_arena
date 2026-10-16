# Tasks

## 1. Dependencies and settings

- [x] 1.1 Add `openai` and `anthropic` to `pyproject.toml` via Poetry and verify `poetry install` succeeds and both SDKs import
- [x] 1.2 Add optional `openai_api_key` and `anthropic_api_key` (`SecretStr | None`) to settings and `.env.example`, and verify unit tests show the keys are masked in `repr` and that `tests/test_config.py` passes

## 2. Interface and schemas

- [x] 2.1 Add `components/third_party/llm/schemas.py` (request, response, usage, model target, provider name and error kind enums) with validation for empty prompt, non-positive timeout/max tokens and temperature outside 0 to 2, and verify unit tests for each invalid case and for null usage
- [x] 2.2 Add `providers/base.py` with `BaseLLMProvider` and `LLMProviderError` (kind, provider, retryable, request id, retry-after) and verify a unit test of the retryable defaults per kind

## 3. Adapters

- [x] 3.1 Add the OpenAI adapter (injectable client, `max_retries=0`, per-request timeout, measured latency, request id, usage, finish reason, error mapping) and verify mocked tests for success, missing usage, missing request id, rate limit with retry-after, authentication, timeout, server error, invalid request and empty content
- [x] 3.2 Add the Anthropic adapter with the same behavior and verify the same mocked test matrix, including multi-block content and a missing usage object

## 4. Service

- [x] 4.1 Add `LLMService` with per-instance lazy provider creation from `ModelTargetSchema`, `UnsupportedProviderError` and `ProviderNotConfiguredError`, and verify tests for two providers in one instance, unsupported provider, missing key (error names the setting, no call made) and no shared state between two service instances
- [x] 4.2 Verify with a test that API keys, prompts and responses do not appear in captured logs or error messages on both success and failure paths

## 5. Real-provider tests and final checks

- [x] 5.1 Add opt-in tests in `tests/llm/real/` (marker `real_provider`, skipped unless `RUN_REAL_PROVIDER_TESTS=1` and keys are set), register the marker, document how to run them in README.md, and verify they are skipped by a plain `pytest`
- [x] 5.2 Run `pytest`, `ruff check .`, `black --check .` and `mypy` and verify all pass, noting any tests that could not run

## Workflow follow-up

- Move Kan card LLM-3 (`kjgut69l123j`) to In-progress when work starts and to Review once the reviewer agent has reviewed it.
- Use a dedicated branch, open a pull request, and archive the change after approval.
