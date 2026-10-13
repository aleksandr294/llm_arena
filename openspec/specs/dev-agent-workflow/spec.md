# dev-agent-workflow Specification

## Purpose

Defines the coder, tester and reviewer subagents used to develop this repository, so implementation, verification and review stay separate and honestly reported.

## Requirements

### Requirement: Role-specific agents are available
The repository SHALL provide three project-level subagents named `coder`, `tester` and `reviewer`, each with a description that states when it should be used.

#### Scenario: Agents are discoverable
- **WHEN** a Claude Code session is opened in the repository
- **THEN** `coder`, `tester` and `reviewer` are listed as available subagents

### Requirement: Coder implements scoped tasks
The `coder` agent SHALL implement only the OpenSpec task it is given, follow the architecture layering and code style in CLAUDE.md, and add no dependencies without stated justification.

#### Scenario: Task stays within scope
- **WHEN** the coder is given a single task from a change's tasks.md
- **THEN** it modifies only files needed for that task and reports the files changed

#### Scenario: Out-of-scope work is surfaced
- **WHEN** the coder finds work outside the assigned task is needed
- **THEN** it reports the need instead of implementing it

### Requirement: Tester reports real results
The `tester` agent SHALL add or update tests for the change and run pytest, Ruff, Black and mypy, and SHALL report each command with its actual outcome.

#### Scenario: All checks pass
- **WHEN** every command exits successfully
- **THEN** the tester reports each as passed together with the command run

#### Scenario: A check fails or cannot run
- **WHEN** a command fails or cannot be executed
- **THEN** the tester reports the failure with its output and does not describe the check as passed

### Requirement: Reviewer is read-only
The `reviewer` agent SHALL be unable to modify files and SHALL review the diff against the OpenSpec change artifacts and CLAUDE.md, returning findings ordered by severity.

#### Scenario: Review produces findings only
- **WHEN** the reviewer finds a defect
- **THEN** it reports the file, location and reason, and leaves the code unchanged

#### Scenario: No findings
- **WHEN** the reviewer finds no valid issues
- **THEN** it states that no findings were identified and what it checked

### Requirement: Agents do not publish or merge
The agents SHALL NOT merge pull requests, push branches, or bypass required checks.

#### Scenario: Publishing is left to the user
- **WHEN** work is complete
- **THEN** the agent reports status and leaves committing, pushing and merging to the main session or the user
