# Tasks

Branch: `feat/tech-debt-llm-providers` (worktree `../llm-arena-tech-debt-providers`).
Kan cards: `dfrgttplkmxj`, `xlzbr92mwozm`.

## 1. Cleanups and error kinds

- [ ] 1.1 Add `LLMErrorKind.CONNECTION` (retryable), make `retry_after` reject `inf`/`nan` via `math.isfinite`, fix the implicit string concatenation in `exceptions.py`; add unit tests (`tests/llm/test_anthropic_provider.py`, `tests/llm/test_openai_provider.py`, plus a utils test) for both kinds, `inf`, `nan`, negative; verify the existing mapping tests still pass.
- [ ] 1.2 Move `_log` and the shared mapping rules into `llm/utils.py` (`log_call`, `map_sdk_error` with an SDK-errors dataclass) and use them in both adapters; verify the adapter tests pass unchanged and `grep -n "def _log\|def _map_error" components/third_party/llm/providers` shows no duplicates.
- [ ] 1.3 Replace `client or AsyncX(...)` with an `is None` check in both adapters; verify with a test that an injected falsy-but-valid client is used.
- [ ] 1.4 Update `docs/experiments-api.md` and `docs/observability.md` for the `connection` kind; verify the kind list in docs matches `LLMErrorKind`.

## 2. Usage on empty and cached responses

- [ ] 2.1 Count Anthropic cache creation/read tokens in input tokens; add tests (cache read present, fields absent, `None`); verify usage stays unknown when the provider reports none.
- [ ] 2.2 Attach `finish_reason` and `usage` to the `malformed_response` error in both adapters; store them in `_failure` in the runner's `LLMProviderError` branch; add adapter tests (OpenAI empty content with `length` and usage) and a runner test; verify the failed row has the token counts and the finish reason, and an error without usage stores null.

## 3. Optional TTFT

- [ ] 3.1 Add the `llm_measure_ttft` setting (`.env.example`, config test) and pass it from `LLMService` through `default_provider_factories(measure_ttft=...)` into the adapter constructors; first verify the installed SDKs expose `messages.stream`/`get_final_message` and `stream_options={"include_usage": True}` (a test that imports and inspects them).
- [ ] 3.2 Implement the Anthropic streaming path with `time_to_first_token_ms`, the overall `asyncio.timeout`, and error mapping; test with fake streams: first chunk timing, empty leading chunks ignored, mid-stream failure, timeout; verify text/usage/request id equal the non-streaming result.
- [ ] 3.3 Implement the OpenAI streaming path the same way (usage chunk, finish reason, `delta.content` None handling); same tests; verify usage is `None` when no usage chunk arrives.
- [ ] 3.4 Add `time_to_first_token_ms` to `LLMResponseSchema`, the `model_call_ttft_seconds` histogram and `record_ttft`, call it from the runner, add the dashboard panel; add tests in `tests/experiments/test_model_metrics.py` (recorded when set, absent when `None`); verify the dashboard JSON still parses.
- [ ] 3.5 Document the setting, the metric and its difference from the speed approximation in `docs/observability.md`; verify the PromQL examples run against the exposed metric names.

## 4. Integration

- [ ] 4.1 Run `pytest`, `ruff check`, `black --check`, `mypy`, Bandit and `openspec validate tech-debt-llm-providers --strict`; report real results. Run the opt-in real-provider tests only when keys are configured, otherwise record them as not run.

## Workflow follow-up

- Move the two Kan cards to `In-progress` at start and to `Review` after the reviewer agent.
- Resolve the `runner.py` merge with `tech-debt-experiment-pipeline` at rebase.
- Archive the change after review requirements are satisfied.
