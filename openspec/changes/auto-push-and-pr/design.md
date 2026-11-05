# Design

## Context

The workflow rules live in prose: `CLAUDE.md` (steps 9-10 and "Do not push,
merge pull requests..."), `README.md` (Development methodology),
`docs/development-agents.md`, and two skills,
`git-conventional-commits` ("Never run `git push`") and `kan-review`. The
subagents already cannot push (`coder.md`, `tester.md`; `reviewer` has no
write tools). The `dev-agent-workflow` spec forbids agents to push. The
GitHub MCP server (`plugin:github:github`) currently fails to connect because
no token is set, `gh` is not installed, and the repo has no `.github/`. The remote is
`https://github.com/aleksandr294/llm_arena`. See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- One place of truth for "when and how the flow publishes", referenced by the
  docs and skills instead of repeated.
- Publishing is gated by explicit preconditions and fails loudly.

**Non-Goals:**
- No GitHub Actions or CI (the "workflow" here is the agent dev flow).
- No auto-merge, no auto-move of the Kan card to `Done`.
- No changes to subagent permissions.

## Decisions

1. **The main session publishes, not a subagent.** Subagents stay
   unprivileged, which keeps the existing spec intact for them and keeps the
   outward-facing action in the one context that sees the whole flow.
   Alternative: a new `publisher` agent. Rejected: extra moving part for two
   commands.

2. **GitHub MCP is the only channel for the PR.** The server is declared in
   `.mcp.json` (HTTP, `Authorization: Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}`),
   so the token stays in the environment and out of the repo. The PR is
   created with the MCP `create_pull_request` tool. Alternatives: the `gh`
   CLI and GitHub REST with curl, both rejected by the owner's decision (one
   channel, no token handling in shell commands). The MCP server cannot push
   local commits, so the branch is pushed with plain `git push`, which uses
   the machine's git credentials. If the MCP server is not connected, the
   flow reports "PR not opened" and does not fall back. The `github` plugin
   is disabled to avoid a duplicate server.

3. **Ordering: checks -> review -> commit -> push -> PR -> Kan comment.** The
   Kan comment comes last so it can carry the PR URL (the `kan-review` format
   already has a `PR:` field). The reviewer still runs before anything is
   published.

4. **Preconditions are checked by the main session before pushing:** branch
   is not `main`; `git status` clean; tester reported pytest, Ruff, Black,
   mypy (and Bandit/pip-audit where the Security section requires them) as
   actually passed; no unaddressed valid reviewer finding. Any failure stops
   publishing and is reported.

5. **Push is `git push -u origin <branch>` only.** No `--force`, no
   `--no-verify`, never `main`. A rejected push is reported. This keeps the
   authorization narrow: it covers publishing a feature branch, nothing that
   rewrites or merges.

6. **PR content.** MCP `create_pull_request` with `base: main`, `head: <branch>`, title in
   Conventional Commits form (same as the main commit), body from the
   Completion Report in `CLAUDE.md`, ending with the session's attribution
   line. The body must not contain secrets or raw sensitive request content.

7. **Authorization is durable and scoped.** The owner's request for this
   change is the explicit authorization that `CLAUDE.md` demands; the text in
   `CLAUDE.md` is changed so push + PR are part of the flow, while "merge,
   force, bypass" keep requiring explicit authorization.

## Risks / Trade-offs

- [Wrong or unfinished work leaves the machine] -> preconditions in decision
  4; PRs are reviewable and closable, and merge stays with the owner.
- [Pushing publishes content that may be cached or indexed] -> the existing
  secret rules apply: staged files are explicit, `.env*` never committed;
  the flow must stop if a file looks like a credential (already in the
  commit skill).
- [MCP server down or token missing/expired] -> loud "PR not opened" report with the branch already pushed; the token setup is one-time.
- [Token scope too broad] -> use a fine-grained token limited to this repository.
- [Lighter flows (docs, refactor) also auto-publish] -> intended; they use
  the same final steps with proportionate checks.
- [The Claude `git-workflow` plugin skill of the same name also says "never
  pushes"] -> it lives outside the repo and is not changed; the repo skill
  and `CLAUDE.md` take precedence for this project.
