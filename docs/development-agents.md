## Development agents

[Back to the README](../README.md)

Project subagents in `.claude/agents/` support the OpenSpec workflow. Run
them in this order for each task:

1. `coder` implements one task from the change's `tasks.md`.
2. `tester` adds tests and runs pytest, Ruff, Black and mypy.
3. `reviewer` does a read-only review of the diff against the change and
   `CLAUDE.md`.

The agents do not commit, push or open pull requests. The main session
commits, pushes the feature branch (`git push -u origin <branch>`) and opens
the pull request against `main` through the GitHub MCP server, only when the
branch is not `main`, the tree is clean, the checks passed and valid review
findings are addressed (see `CLAUDE.md`). Merging stays with the owner.

### GitHub MCP prerequisites

- `GITHUB_PERSONAL_ACCESS_TOKEN` is set in the environment (fine-grained
  token for this repository with Contents and Pull requests read/write); the
  token is never written into `.mcp.json`.
- The Claude `github` plugin is disabled so the server is not declared twice.
- `claude mcp list` shows `github` as connected. Otherwise the flow reports
  "PR not opened" and does not fall back to another tool.
