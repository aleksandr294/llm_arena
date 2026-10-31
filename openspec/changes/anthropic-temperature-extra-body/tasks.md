# Tasks

## 1. Anthropic adapter

- [x] 1.1 In `components/third_party/llm/providers/anthropic.py` send the temperature as `extra_body={"temperature": ...}` only when set, with a comment on why; verify `poetry run pytest tests/llm/test_anthropic_provider.py` passes
- [x] 1.2 Update `test_optional_parameters_are_forwarded` to assert `kwargs["extra_body"] == {"temperature": 0.5}` and that `temperature` is not a keyword; keep the omitted case asserting neither is present; verify the tests pass
- [x] 1.3 Add a test that calls `generate` with every optional field set and asserts all keyword names are in `inspect.signature(AsyncMessages.create).parameters`; verify it fails against the old adapter code (temporarily restore it) and passes against the new one

## 2. Checks

- [x] 2.1 Run `poetry run pytest -m "not integration"`, `ruff`, `black --check`, `mypy` and Bandit (command in `docs/security.md`); verify all are clean
- [x] 2.2 Check `docs/` for text that says Anthropic temperature is sent as a named argument or is unsupported, and fix it; verify with `grep -rn temperature docs README.md`

## Workflow follow-up

- Review the diff with the reviewer agent, then commit on `fix/anthropic-temperature` following `git-conventional-commits`. Do not push.
- Archive the change after the pull request is merged.
