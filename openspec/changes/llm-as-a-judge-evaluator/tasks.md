# Tasks

## 1. Configuration schema

- [x] 1.1 Add `LlmJudgeParams` (`criteria: str` with a length limit, `provider: ProviderName`, `model: str`) and `LlmJudgeEntry(_Entry, LlmJudgeParams)` with `type: Literal["llm_judge"]` to `components/evaluation/schemas.py`; add it to the `EvaluatorEntry` discriminated union; verify a unit test parses a config with one `llm_judge` entry alongside a deterministic one
- [x] 1.2 Reject missing/empty `criteria`, an unsupported `provider` value, and a `criteria` string over the length limit with a validation error; verify unit tests for each rejection, and an API test that experiment creation with each invalid shape answers 422 naming the field

## 2. Judge call and verdict parsing

- [x] 2.1 Add `components/evaluation/judge.py` (or similar) with the fixed system prompt template (criteria placeholder, delimited data blocks for input/response/expected output, instructs a single leading `PASS`/`FAIL` token) and a pure verdict parser (`PASS`/`FAIL` case-insensitive token → `OutcomeStatus.PASSED`/`FAILED`; anything else → `None`); verify unit tests for a clean pass, a clean fail, an ambiguous response, an empty response and a response that only contains explanation text with no token
- [x] 2.2 Add a hostile-input test (using `tests/security/helpers.py`'s corpus) feeding adversarial text as the response being judged and asserting none of it reaches the built prompt's data blocks incorrectly (i.e. the delimiters are not breakable by the hostile text) and none of it reaches the parsed verdict path; verify it passes

## 3. Two-phase evaluation

- [x] 3.1 In `components/evaluation/repository.py`, extend `get_input`/`EvaluationInput` to also select the test case's `input`; verify an existing repository test still passes and a new one asserts the field is populated
- [x] 3.2 In `components/evaluation/service.py`, split `evaluate_result` into the existing deterministic phase (unchanged behavior) and a new judge phase: for each `llm_judge` entry, call `LLMService.generate(...)` with `temperature=0` and no session open, parse the verdict, and on a retryable `LLMProviderError` retry once with `backoff_delay` before giving up; store `OutcomeStatus.ERROR` (detail: error/exception kind only, never provider text) on failure or an unparseable verdict; insert all judge-phase rows via `EvaluationRepository.insert_ignore` in one new transaction after every call has returned; verify a unit test (mocked `LLMService`) covers: a result with only deterministic evaluators behaves exactly as before (no judge call made), a result with a passing judge call, a failing judge call, a retried-then-successful call, a non-retryable provider error, and an unparseable response
- [x] 3.3 Confirm no database session or transaction is open during the judge call: verify with a test that raises if the injected `AsyncSession` is used between the deterministic phase's commit and the judge phase's insert (a spy/fake session, or inspecting that `session_scope()` is not re-entered mid-call)

## 4. Wiring

- [x] 4.1 Add an `llm_service` constructor parameter to `EvaluationService`; update `components/evaluation/dependencies.py`'s `build_evaluation_service` to accept and pass it through; update `components/evaluation/tasks.py` to build one `LLMService(get_settings())` per process (mirroring `components/experiments/tasks.py`'s `get_runner()`) and pass it in; verify `poetry run pytest tests/evaluation` passes
- [x] 4.2 Confirm the HTTP-triggered evaluation paths (any endpoint that calls `EvaluationService` directly rather than through the task) also receive an `LLMService`; verify with the existing endpoint tests plus a new one exercising an experiment with an `llm_judge` evaluator end to end (integration test, needs PostgreSQL)

## 5. Docs and checks

- [x] 5.1 Update `docs/evaluation-api.md` with the `llm_judge` evaluator type, its configuration fields, the pass/fail/error outcome shape, and the documented limitation that its explanation text is never stored and prompt injection is mitigated, not prevented
- [x] 5.2 Update `tests/security/helpers.py`'s docstring note about the future LLM-as-a-judge feature now that it exists, pointing at the tests added in 2.2
- [x] 5.3 Run `poetry run pytest -m "not integration"`, `ruff`, `black --check`, `mypy`, and Bandit at medium severity and above (command in `docs/security.md`); verify all are clean
- [x] 5.4 Run the integration tests (`poetry run pytest -m integration`) against a scratch PostgreSQL database per `docs/testing.md`; verify they pass, or record exactly what could not be run (752 passed, 1 failed on first full run: tests/auth/test_cli.py::test_no_http_route_creates_users, a pre-existing fastadmin import-order test-isolation issue unrelated to this change — confirmed passing standalone)

## Workflow follow-up

- Review the diff with the reviewer agent, then commit on `feat/llm-as-a-judge-evaluator` following `git-conventional-commits`. Do not push.
- Move the Kan card to Review once reviewed, then to Done only after the user merges the pull request.
- Archive this change after the pull request is merged.
