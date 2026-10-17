# Spec Delta

## MODIFIED Requirements

### Requirement: Create an experiment
The system SHALL create an experiment owned by the authenticated user from a dataset visible to that user, 1 to 10 distinct provider/model targets, a prompt template, generation parameters and an evaluator configuration. The response SHALL be 201 with status pending, and no model request SHALL be made.

#### Scenario: Experiment created
- **WHEN** a client creates an experiment for a dataset with 3 cases and 2 models
- **THEN** the response is 201, the status is pending and 6 pending results exist

#### Scenario: Unknown dataset
- **WHEN** a client creates an experiment for a dataset that does not exist
- **THEN** the response is 404 and nothing is stored

#### Scenario: Owner recorded
- **WHEN** a staff user creates an experiment for their dataset
- **THEN** the experiment's owner is that user and its organization is the dataset's organization

## ADDED Requirements

### Requirement: List experiments
The system SHALL list the experiments visible to the caller, oldest first, with pagination (limit 1 to 100, default 50, offset 0 or more), a total, and optional filters by status and by creation time (`created_from` and `created_to`, both inclusive), and for admins and superadmins by owner.

#### Scenario: Status filter
- **WHEN** a client filters experiments by status completed
- **THEN** only visible completed experiments are returned and the total reflects the filter

#### Scenario: Date and status combined
- **WHEN** a client filters by a creation time range and a status
- **THEN** only experiments matching both are returned

#### Scenario: Invalid status
- **WHEN** a client filters by an unknown status
- **THEN** the response is 422
