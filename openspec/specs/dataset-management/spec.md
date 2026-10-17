# dataset-management Specification

## Purpose

Defines how datasets of evaluation test cases are created, stored and read, so that later experiments can run identical inputs against several models.

## Requirements

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

### Requirement: Dataset input validation
The system SHALL reject a dataset whose name is missing, blank after trimming whitespace, longer than 255 characters or contains a NUL character, and a description longer than 2000 characters, with 422 and an error naming the invalid field.

#### Scenario: Blank name
- **WHEN** a client creates a dataset whose name contains only whitespace
- **THEN** the response is 422 and the error identifies the name field

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

### Requirement: Get a dataset
The system SHALL return a dataset by id together with the number of test cases it contains.

#### Scenario: Existing dataset
- **WHEN** a client requests an existing dataset that has 2 test cases
- **THEN** the response contains the dataset with a case count of 2

#### Scenario: Unknown dataset
- **WHEN** a client requests a dataset id that does not exist
- **THEN** the response is 404 with a message that the dataset was not found

#### Scenario: Malformed id
- **WHEN** a client requests a dataset with an id that is not a valid UUID
- **THEN** the response is 422

### Requirement: Add a test case
The system SHALL allow a test case to be added to an existing dataset with a non-empty input, an optional expected output and optional metadata (a JSON object). The test case SHALL receive the next position in its dataset, starting at 1, and the response SHALL be 201.

#### Scenario: First and second case
- **WHEN** two test cases are added to an empty dataset in order
- **THEN** they have positions 1 and 2

#### Scenario: Expected output omitted
- **WHEN** a test case is added without an expected output
- **THEN** its expected output is null, not an empty string

#### Scenario: Case for unknown dataset
- **WHEN** a test case is added to a dataset id that does not exist
- **THEN** the response is 404 and nothing is stored

### Requirement: Test case input validation
The system SHALL reject a test case whose input is missing, empty, longer than 50000 characters or contains a NUL character, an expected output longer than 50000 characters or containing a NUL character, or metadata that is not a JSON object, with 422 and an error naming the invalid field.

#### Scenario: Empty input
- **WHEN** a client adds a test case with an empty input
- **THEN** the response is 422 and the error identifies the input field

#### Scenario: Metadata not an object
- **WHEN** a client adds a test case whose metadata is a JSON array
- **THEN** the response is 422

### Requirement: Read test cases
The system SHALL list a dataset's test cases ordered by position, with the same pagination and total count as dataset listing, and SHALL return stored values exactly as supplied.

#### Scenario: Ordered listing
- **WHEN** a dataset has three test cases
- **THEN** listing returns them in positions 1, 2, 3

#### Scenario: Content preserved
- **WHEN** a test case containing quotes, newlines and non-ASCII text is added and then read
- **THEN** the returned input equals the submitted input exactly

#### Scenario: Cases of unknown dataset
- **WHEN** test cases are listed for a dataset id that does not exist
- **THEN** the response is 404

### Requirement: Stored content is untrusted data
Dataset and test case content SHALL be stored and returned as data only and SHALL NOT be interpreted or executed, and SHALL NOT be written to logs.

#### Scenario: Markup in input
- **WHEN** a test case input contains HTML or template syntax
- **THEN** it is stored and returned unchanged
