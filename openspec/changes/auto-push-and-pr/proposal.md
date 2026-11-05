# Proposal

## Why

The development workflow ends with a local commit, and the owner has to push
the branch and open the pull request by hand every time. The owner now wants
the workflow to finish the job itself: once the implementation, checks and
independent review are done, the branch is pushed and a GitHub pull request
is opened. This change records that authorization and updates the rules that
currently forbid it.

## What Changes

- At the end of the development flow, after the commit, the main session
  pushes the feature branch to `origin` with `git` and opens a pull request
  against `main` on GitHub through the GitHub MCP server, without asking
  again. The GitHub MCP server is the only way a PR is opened; the `gh` CLI
  is not used.
- Push and PR happen only when the preconditions hold: not on `main`, the
  working tree is clean, the quality gates passed, and the reviewer's valid
  findings are addressed. Otherwise the flow stops and reports why.
- The PR description follows the Completion Report from `CLAUDE.md` and ends
  with the attribution line required by the session.
- The PR URL is reported to the user and written into the Kan review comment.
- Merging, force-pushing, pushing to `main` and bypassing checks stay
  forbidden without explicit authorization.
- Subagents (`coder`, `tester`, `reviewer`) stay unable to push or open PRs;
  only the main session does.
- Declare the GitHub MCP server in the project's `.mcp.json` (token read
  from `GITHUB_PERSONAL_ACCESS_TOKEN`, never stored in the file).
- Update `CLAUDE.md`, `README.md`, `docs/development-agents.md`, the
  `git-conventional-commits` skill and the `kan-review` skill to match.

## Capabilities

### New Capabilities

### Modified Capabilities
- `dev-agent-workflow`: the requirement "Agents do not publish or merge" is
  replaced: subagents never publish, while the main session pushes the
  branch and opens a PR at the end of a successful flow; merging remains the
  user's.

## Impact

- Docs and agent/skill instructions only: `CLAUDE.md`, `README.md`,
  `docs/development-agents.md`, `.mcp.json`, `.claude/skills/git-conventional-commits`,
  `.claude/skills/kan-review`, and `openspec/specs/dev-agent-workflow` (via
  the delta at archive).
- No application code, API, migration or dependency changes.
- Prerequisite on the developer machine: `GITHUB_PERSONAL_ACCESS_TOKEN` set
  in the environment (fine-grained token with Contents, Pull requests:
  read/write) and the `github` MCP server connected. The `github` plugin
  must be disabled so the server is not declared twice.
- Outward-facing effect: pushed branches and open PRs on
  `github.com/aleksandr294/llm_arena`.
