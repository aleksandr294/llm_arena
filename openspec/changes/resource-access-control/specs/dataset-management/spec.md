# Spec Delta

## MODIFIED Requirements

### Requirement: Create a dataset
The system SHALL allow an authenticated user with an organization to create a dataset with a name and an optional description. The response SHALL be 201 and contain the dataset's id, name, description, owner, organization and creation time.

#### Scenario: Dataset created
- **WHEN** a client creates a dataset with a valid name
- **THEN** the response is 201 and the dataset can be read back by its id

#### Scenario: Description omitted
- **WHEN** a client creates a dataset without a description
- **THEN** the dataset is created and its description is null

#### Scenario: Owner recorded
- **WHEN** a staff user creates a dataset
- **THEN** the response names that user as owner and their organization

### Requirement: List datasets
The system SHALL list the datasets visible to the caller in a stable order, oldest first, with pagination by limit (1 to 100, default 50) and offset (0 or more), SHALL return the total count of visible datasets, and SHALL support optional filters by creation time (`created_from` inclusive, `created_to` inclusive) and, for admins and superadmins, by owner.

#### Scenario: Pagination
- **WHEN** 3 visible datasets exist and a client requests limit 2 and offset 2
- **THEN** the response contains the third dataset and a total of 3

#### Scenario: Invalid pagination
- **WHEN** a client requests a limit of 0 or a negative offset
- **THEN** the response is 422

#### Scenario: Date filter
- **WHEN** a client filters by a creation time range
- **THEN** only visible datasets created within the range are returned and the total reflects the filter

#### Scenario: Invalid range
- **WHEN** created_from is later than created_to
- **THEN** the response is 422
