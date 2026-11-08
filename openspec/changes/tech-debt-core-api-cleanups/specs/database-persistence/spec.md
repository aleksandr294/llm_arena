# Spec Delta

## ADDED Requirements

### Requirement: Commit callbacks are reliable
The system SHALL report every failure of an after-commit callback (the first one raised, the others logged by type), and SHALL discard the registered callbacks when the commit itself fails so they cannot run after a later successful commit.

#### Scenario: Two callbacks fail
- **WHEN** two after-commit callbacks raise
- **THEN** the first error is raised and the second is logged with its exception type

#### Scenario: Failed commit
- **WHEN** a commit fails and the session is committed again later
- **THEN** the callbacks registered before the failed commit do not run
