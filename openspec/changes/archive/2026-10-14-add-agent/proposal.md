# Proposal

## Why

CLAUDE.md prescribes a workflow of implement → test/lint/type-check → independent review for every OpenSpec task, but nothing in the repository encodes it. Today each step depends on ad-hoc prompting, and review is performed in the same context that wrote the code, which weakens it as an independent check.

## What Changes

- Add three project-level Claude Code subagents under `.claude/agents/`:
  - `coder`: implements one OpenSpec task within the architecture and style rules of CLAUDE.md.
  - `tester`: writes or extends tests for the change and runs pytest, Ruff, Black and mypy, reporting actual results.
  - `reviewer`: read-only review of the diff against the OpenSpec change and CLAUDE.md, returning findings.
- Each agent gets a restricted tool set matching its role (the reviewer cannot edit files).
- Document in the agent definitions the hand-off contract between the three roles and the rule that results are only reported as passing when commands actually ran successfully.
- No backend code, API, database or dependency changes.

## Capabilities

### New Capabilities
- `dev-agent-workflow`: Contract for the repository's coder, tester and reviewer subagents: their responsibilities, tool restrictions, and reporting rules.

### Modified Capabilities

## Impact

- New files in `.claude/agents/` (three markdown definitions).
- No change to application code, migrations, dependencies or CI.
- Agents operate under the existing branch/PR rules in CLAUDE.md; they do not merge or push on their own.

## Out of Scope

- An agent feature inside the LLM Arena platform (agents as experiment targets or judges).
- Orchestrating the three agents automatically (a slash command or skill chaining them) — they are invoked by the main session.
