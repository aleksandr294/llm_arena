# Spec Delta

## MODIFIED Requirements

### Requirement: Agents do not publish or merge
The `coder`, `tester` and `reviewer` subagents SHALL NOT commit, push branches, open or merge pull requests, or bypass required checks. No agent SHALL merge a pull request.

#### Scenario: Publishing is left to the user
- **WHEN** a subagent finishes its task
- **THEN** it reports status and leaves committing, pushing and PR creation to the main session

#### Scenario: Merge is left to the user
- **WHEN** a pull request has been opened
- **THEN** no agent merges it and merging is left to the user

## ADDED Requirements

### Requirement: Finished flow publishes a pull request
When a development flow finishes successfully, the main session SHALL commit on the feature branch, push it to `origin` and open a GitHub pull request against `main` without asking for further confirmation.

#### Scenario: Flow finishes successfully
- **WHEN** implementation, quality checks and independent review are complete and the work is committed on a feature branch
- **THEN** the branch is pushed with upstream tracking and a pull request against `main` is opened through the GitHub MCP server
- **AND** the pull request URL is reported to the user

#### Scenario: Pull request description
- **WHEN** the pull request is opened
- **THEN** its title follows Conventional Commits and its description contains the completion report: what changed and why, checks run with their real results, migrations or configuration changes, and known limitations

### Requirement: Publishing preconditions
The main session SHALL push and open a pull request only when the current branch is not `main`, the working tree is clean, the required quality checks have passed, and valid review findings are addressed.

#### Scenario: Precondition not met
- **WHEN** the branch is `main`, the tree has uncommitted changes, a required check failed or was not run, or a valid review finding is unaddressed
- **THEN** nothing is pushed and the main session reports which precondition failed

#### Scenario: GitHub MCP server unavailable
- **WHEN** the GitHub MCP server is not connected or not authenticated
- **THEN** the main session reports that the PR was not opened and why, does not describe it as completed, and does not fall back to another tool

#### Scenario: GitHub MCP is the only PR channel
- **WHEN** a pull request is opened
- **THEN** it is created through the GitHub MCP server

### Requirement: Publishing is non-destructive
Publishing SHALL be limited to a normal push of the feature branch. The main session SHALL NOT force-push, push to `main`, merge, or skip hooks or checks without explicit user authorization.

#### Scenario: Push is rejected
- **WHEN** the remote rejects the push, for example because the branch diverged
- **THEN** the main session reports the rejection and does not force-push

### Requirement: Review record links the pull request
The Kan review comment SHALL include the pull request URL when one was opened, and the card SHALL NOT be moved to `Done` by an agent.

#### Scenario: PR opened before review comment
- **WHEN** the review outcome is posted on the Kan card after the pull request is opened
- **THEN** the comment's PR field holds the pull request URL and the card moves to `Review`
