# Tasks

## 1. Workflow rules and docs

- [x] 1.0 Commit `.mcp.json` (GitHub MCP, token via `scripts/github-mcp-headers.sh` from the environment or `.env`) and verify `python3 -m json.tool .mcp.json` passes and the file contains no token value.

- [x] 1.1 Update `CLAUDE.md` Development Workflow (steps 9-10, the "Do not push..." line) and Completion Report so push + PR are part of a finished flow with the preconditions, while merge, force-push, pushing `main` and bypassing checks still need explicit authorization. Verify: `grep -n -i "push" CLAUDE.md` shows no line that contradicts the new rule.
- [x] 1.2 Update the `git-conventional-commits` skill: description and Rules say commit first, then push the feature branch and open the PR per the flow (never force, never `main`, never `--no-verify`); add a "Publishing" section with the precondition list, `git push -u origin <branch>` and opening the PR only through the GitHub MCP `create_pull_request` tool (`base: main`) with title/body rules; state that `gh` is not used. Verify: the skill has no remaining "never pushes" statement and no `gh` instruction.
- [x] 1.3 Update the `kan-review` skill so the comment is posted after the PR is opened and its `PR:` field holds the URL (or "none" with the reason). Verify: the format block and ordering text reflect this.
- [x] 1.4 Update `README.md` (Development methodology: Commits, Review) and `docs/development-agents.md` (main session commits, pushes and opens the PR; subagents never do; GitHub MCP prerequisites: `GITHUB_PERSONAL_ACCESS_TOKEN`, plugin `github` disabled, `claude mcp list` shows `github` connected). Verify: both pages state the same rule as `CLAUDE.md`, and relative links still resolve.
- [x] 1.5 Confirm subagent files (`coder.md`, `tester.md`, `reviewer.md`) still forbid push/PR and need no change. Verify: `grep -n -i "push" .claude/agents/*.md`.

## 2. Integration check

- [x] 2.1 Run `openspec validate auto-push-and-pr --strict` and verify it passes; confirm the delta's MODIFIED requirement header matches `dev-agent-workflow` exactly.
- [x] 2.2 Dry-run the precondition logic on the branch of this change without pushing: verify `git branch --show-current` is not `main`, `git status --porcelain` is empty after commit, and `claude mcp list` result for `github` is reported truthfully (expected until the token is set: failed, so the flow reports "PR not opened").

## Workflow follow-up

- Set `GITHUB_PERSONAL_ACCESS_TOKEN` in the environment or `.env`, disable the `github` plugin and restart Claude Code before the first auto-publish.
- Archive the change after review requirements are satisfied, then verify `openspec/specs/dev-agent-workflow/spec.md`.
