# Spec Delta

## ADDED Requirements

### Requirement: Per-organization active experiment limit
The system SHALL reject starting an experiment with 429 when the caller's organization already has the configured maximum number of experiments that are pending or running, and SHALL NOT count finished experiments.

#### Scenario: Limit reached
- **WHEN** an organization with the maximum number of running experiments starts another
- **THEN** the response is 429 and the experiment stays not started

#### Scenario: Finished experiments do not count
- **WHEN** the running experiments of an organization complete
- **THEN** a new experiment can be started
