---
name: git-conventional-commits
description: Write and create git commits for this project following Conventional Commits 1.0.0. Use whenever committing changes, finishing a development flow, or when asked for a commit message. After committing, the finished flow pushes the feature branch and opens a PR through the GitHub MCP server; never force, never `main`.
---

# Git commits (Conventional Commits 1.0.0)

Spec: https://www.conventionalcommits.org/en/v1.0.0/

## Format

```
<type>[optional scope][!]: <description>

[optional body]

[optional footer(s)]
```

- **type** (lowercase): `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.
- **scope** (optional, lowercase noun in parentheses): the component or area, e.g. `datasets`, `llm`, `core`, `migrations`, `api`, `kan`, `openspec`. Example: `feat(llm): add OpenAI adapter`.
- **description**: imperative mood, lowercase start, no trailing period, at most 72 characters.
- **body** (optional): explain what and why, not how; blank line after the description; wrap at about 80 columns.
- **footers** (optional): `Refs: LLM-3`, `Co-Authored-By: ...`, etc.
- **Breaking change**: add `!` after the type/scope and/or a `BREAKING CHANGE: <description>` footer.

## Choosing the type

- New capability or endpoint: `feat`. Bug fix: `fix`.
- Only tests: `test`. Only docs/README/CLAUDE.md: `docs`.
- Dependencies, Dockerfile, pyproject, lock files: `build`. CI config: `ci`.
- Repo tooling, scripts, config with no product effect: `chore`.
- Behavior-preserving code change: `refactor`. Formatting only: `style`.
- OpenSpec artifacts of a change (proposal, design, specs, tasks): `docs(openspec)`.

## Procedure

1. Confirm you are on a feature branch, not `main` (see CLAUDE.md). If on `main`, create the branch first.
2. Run `git status` and `git diff` to see what is being committed.
3. Split into logical commits when the diff mixes concerns (for example: openspec artifacts, implementation, dependencies). One commit = one purpose, and each commit should leave the repo passing.
4. Stage files explicitly by path. Never `git add -A` blindly, and never stage `.env`, `.env.kan`, API keys or other secrets, or unrelated user changes. If `.env.example` or any file contains something that looks like a real credential, stop and report instead of committing.
5. Commit with the message passed via a heredoc so formatting is preserved. Append the attribution line required by the session instructions, if any.
6. Show `git log --oneline -n <count>` to report what was created.
7. When the development flow is finished, publish as described in "Publishing" below.

## Rules

- Commit first, then publish the feature branch per "Publishing". Never push `main`, never `--force`, never `--no-verify`, never merge a PR.
- Never amend, rebase, force anything, or skip hooks (`--no-verify`) unless explicitly asked. If a pre-commit hook fails, fix the issue and create a new commit.
- Do not commit when checks relevant to the change are failing; report instead.

## Publishing

At the end of a finished development flow the main session pushes and opens the PR without asking again. Subagents never do this.

Preconditions (all must hold, otherwise push nothing and report which failed):
- `git branch --show-current` is not `main`.
- `git status --porcelain` is empty.
- The required checks (pytest, Ruff, Black, mypy, plus Bandit and pip-audit where CLAUDE.md requires them) were actually executed and passed.
- Valid reviewer findings are addressed.

Steps:
1. `git push -u origin <branch>`. A rejected push is reported, never forced.
2. Open the PR only with the GitHub MCP `create_pull_request` tool: `base: main`, `head: <branch>`, title in Conventional Commits form (same as the main commit), body from the Completion Report in CLAUDE.md (what and why, checks with real results, migrations/config, limitations) ending with the session's PR attribution line. No secrets or raw sensitive request content in the body.
3. Report the PR URL. If the GitHub MCP server is not connected, report "PR not opened" with the reason; do not fall back to another tool. `gh` is not used.

## Examples

```
feat(llm): add provider interface with OpenAI and Anthropic adapters

Normalized response with token usage, measured latency and request id.
Unavailable metrics are null, never invented.

Refs: LLM-3
```

```
docs(openspec): propose provider-abstraction change
```

```
build: add openai and anthropic SDK dependencies
```

```
fix(datasets)!: reject NUL characters in metadata

BREAKING CHANGE: requests whose metadata contains NUL now return 422.
```
