# Proposal

## Why

Kan card LLM-7 ("optional-judge: LLM-as-a-judge, later"). Deterministic evaluators (`exact_match`, `contains`, `not_contains`, `length_between`, `json_valid`, `json_fields`) cover checks a pure function can decide, but cannot judge qualities like relevance, correctness against a free-form reference, or tone. The card defers this until the base loop and reproducibility are stable; both prerequisite cards (4. deterministic-evaluation, 5. metrics-and-comparison) are Done, and CLAUDE.md's own ordering ("Support deterministic evaluators before introducing LLM-as-a-judge") is satisfied.

## What Changes

- Add a new evaluator type, `llm_judge`, alongside the existing deterministic ones in the same `evaluators` configuration list. An operator sets a free-text `criteria` string and a judge `provider`/`model`, independent of the experiment's own target models (confirmed with the user).
- The judge calls the chosen provider through the existing `LLMService`, the same adapter layer `ExperimentRunner` uses, and returns a binary verdict (`passed`/`failed`), using the existing `OutcomeStatus` with no schema change to the verdict itself (confirmed with the user: pass/fail, not a numeric score).
- Because a judge call is slow and non-deterministic, unlike every current evaluator, it cannot share deterministic evaluators' single-transaction, synchronous path. `EvaluationService.evaluate_result` splits into two phases: deterministic evaluators exactly as today (unchanged), then `llm_judge` entries evaluated with no database session open during the call, mirroring `ExperimentRunner`'s claim/call/persist split.
- The judge's prompt includes the test case's input, the model's response and (when present) the expected output, wrapped so the system prompt instructs the judge to treat them as data to evaluate, never as instructions — a defense-in-depth measure against a hostile response trying to manipulate its own verdict, not a guarantee. The judge's own free-text rationale is never stored: `details` keeps the project's existing rule (reason codes and configured identifiers only, never response text), so a judge that echoes hostile content back in its explanation cannot leak it into stored data.
- A judge call that errors (timeout, rate limit, malformed output) stores `OutcomeStatus.ERROR` with the exception/error kind only, consistent with how deterministic evaluators already handle exceptions — never left pending forever.
- Hostile-input tests extend the existing corpus (`tests/security/helpers.py`) to the judge's prompt-building and verdict-parsing, closing the gap that module's own docstring already flags ("A future LLM-as-a-judge feature must run this corpus too").

**Out of scope:**
- Numeric/graded scoring, built-in rubric templates, or an aggregate "judge model" setting shared across evaluators — all explicitly decided against for this change.
- Retrying a judge call beyond the project's existing bounded backoff pattern; no new per-organization cost cap (tracked separately — see the existing tech-debt card on experiment spend caps).

## Capabilities

### New Capabilities
- `llm-judge-evaluation`: the `llm_judge` evaluator type, its configuration, how a judge call is made and bounded, and how its verdict is stored.

### Modified Capabilities

## Impact

- Code: `components/evaluation/schemas.py` (new `LlmJudgeParams`/`LlmJudgeEntry`), `components/evaluation/evaluators.py` (judge is not a `BaseEvaluator` subclass — it needs I/O; a sibling path), `components/evaluation/service.py` (two-phase `evaluate_result`), `components/evaluation/repository.py` (`get_input` also selects the test case's `input`), `components/evaluation/dependencies.py` (wires an `LLMService` into `EvaluationService`), `components/evaluation/tasks.py` (constructs the service with `LLMService`, mirroring `components/experiments/tasks.py`'s `get_runner()`).
- No new settings beyond what `LLMService` already reads (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`); the judge's provider/model come from the evaluator configuration, not global settings.
- No database migration: evaluation outcomes already store arbitrary `details` and a `status`; no new table or column.
- No API breaking change: `llm_judge` is one more accepted value in the existing `type` discriminator of the evaluator configuration.
