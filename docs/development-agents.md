## Development agents

[Back to the README](../README.md)

Project subagents in `.claude/agents/` support the OpenSpec workflow. Run
them in this order for each task:

1. `coder` implements one task from the change's `tasks.md`.
2. `tester` adds tests and runs pytest, Ruff, Black and mypy.
3. `reviewer` does a read-only review of the diff against the change and
   `CLAUDE.md`.

The agents do not commit, push or merge; that stays with the main session.
