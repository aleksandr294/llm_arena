# Tasks

## 1. Agent definitions

- [x] 1.1 Create `.claude/agents/coder.md` (frontmatter name, description, tools; prompt: scope to one task, follow CLAUDE.md layering/style, no new deps, no commit/push) and verify the file has valid frontmatter and appears in the agent list
- [x] 1.2 Create `.claude/agents/tester.md` (tools for tests and commands; prompt: add/extend tests, run pytest, Ruff, Black, mypy via Poetry, report actual outcomes) and verify frontmatter and agent listing
- [x] 1.3 Create `.claude/agents/reviewer.md` with no Edit/Write tools (prompt: review `git diff` against change artifacts and CLAUDE.md, findings by severity, no mutating commands) and verify its tools list excludes Edit and Write

## 2. Validation

- [x] 2.1 Run `openspec validate add-agent --strict` and verify it passes
- [x] 2.2 Dry-run the loop on a trivial task: invoke coder, tester, reviewer in order and verify each returns a report matching the spec scenarios (failures reported as failures, reviewer made no file changes via `git status`)
- [x] 2.3 Mention the three agents and the intended coder → tester → reviewer order in README.md and verify the section renders and matches the agent names

## Workflow follow-up

- Request independent review of the change, open a pull request, and archive the change after approval.
