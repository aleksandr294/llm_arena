# Spec Delta

## ADDED Requirements

### Requirement: Case count limit
The system SHALL reject adding a test case to a dataset that already holds the configured maximum number of cases (default 10,000) with 409 and SHALL store nothing.

#### Scenario: Dataset full
- **WHEN** a client adds a case to a dataset with the maximum number of cases
- **THEN** the response is 409 and the case count is unchanged

#### Scenario: Concurrent adds at the limit
- **WHEN** two cases are added at the same time to a dataset one short of the limit
- **THEN** exactly one succeeds

### Requirement: Case metadata bounds
The system SHALL reject test case metadata nested deeper than 5 levels, with more than 100 keys in total, or larger than 16 KiB when serialized as JSON, with 422 naming the metadata field.

#### Scenario: Too deep
- **WHEN** metadata is nested 6 levels deep
- **THEN** the response is 422 and the error identifies the metadata field

#### Scenario: At the boundary
- **WHEN** metadata is exactly 5 levels deep and 16 KiB serialized
- **THEN** the case is accepted
