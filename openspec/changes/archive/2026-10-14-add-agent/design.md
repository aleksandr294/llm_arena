# Design

## Context

The repository has no `.claude/agents/` directory; only skills and commands for OpenSpec exist. CLAUDE.md defines the workflow (implement, run pytest/Ruff/Black/mypy, independent review, report) and tooling (Poetry, Black 80 cols, Ruff, mypy, pytest). See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- Encode the coder → tester → reviewer loop as three focused subagents.
- Make review independent (separate context, no write tools).

**Non-Goals:**
- Automatic orchestration or looping between agents.
- Any change to application code, CI or dependencies.

## Decisions

- **Three agents instead of one.** Separate contexts keep the reviewer independent of the author's reasoning and keep each prompt short. Alternative: a single agent with phases; rejected because self-review is weaker and tool restrictions cannot differ per phase.
- **Tool restrictions per role.** `coder`: Read, Edit, Write, Grep, Glob, Bash. `tester`: same, with instructions to touch only tests unless a bug is reported back. `reviewer`: Read, Grep, Glob, Bash (git diff/log only by instruction); no Edit/Write. Bash cannot be fully sandboxed by tool lists, so the reviewer prompt forbids mutating commands.
- **Agents read context, not duplicate it.** Prompts point to CLAUDE.md and the active OpenSpec change instead of copying rules, so they do not drift.
- **Honest reporting is part of each prompt**, matching CLAUDE.md ("never claim tests passed unless executed").
- **Model.** Inherit the session model by default; no pinned model IDs.
- **No orchestration file.** The main session invokes agents in sequence; chaining can be a later change.

## Risks / Trade-offs

- [Reviewer Bash access could mutate state] → prompt prohibits it; consider a hook later.
- [Prompts drift from CLAUDE.md] → they reference CLAUDE.md rather than restating it.
- [Agents commit/push unintentionally] → prompts forbid it explicitly.
