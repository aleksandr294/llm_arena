# Spec Delta

## Purpose

Defines who may see and act on datasets, experiments and their derived data: every request is authenticated, every created resource has an owner, and visibility follows the caller's role, without revealing resources outside it.

## ADDED Requirements

### Requirement: Authentication required
The system SHALL require a valid access token for every endpoint except the health endpoints and the login, refresh, logout and validate endpoints, and SHALL answer a missing, malformed or invalid token with 401 and a `WWW-Authenticate: Bearer` header.

#### Scenario: Missing token
- **WHEN** a client lists datasets without an Authorization header
- **THEN** the response is 401

#### Scenario: Health stays public
- **WHEN** a client calls a health endpoint without a token
- **THEN** the endpoint answers normally

### Requirement: Ownership recorded
The system SHALL record the creating user and their organization on every dataset and experiment at creation, and SHALL take them from the access token's user, never from the request body.

#### Scenario: Owner set
- **WHEN** a staff user creates a dataset
- **THEN** the dataset's owner is that user and its organization is the user's organization

#### Scenario: Body cannot override
- **WHEN** a request body names a different owner or organization
- **THEN** the field is rejected or ignored and the caller is recorded

### Requirement: Role-based visibility
The system SHALL let a staff user see and act on only the datasets and experiments they own, an admin those of their organization, and a superadmin all of them, for reading, listing, adding cases, starting, cancelling, retrying, evaluating, viewing metrics and exporting.

#### Scenario: Staff isolation
- **WHEN** staff user A lists datasets while staff user B in the same organization owns datasets
- **THEN** A sees only A's datasets

#### Scenario: Admin scope
- **WHEN** an admin lists experiments
- **THEN** experiments of all users in the admin's organization are returned and none from other organizations

#### Scenario: Superadmin scope
- **WHEN** a superadmin lists datasets
- **THEN** datasets of every organization are returned

### Requirement: Hidden existence
The system SHALL answer a request for a dataset, experiment or any resource derived from them that is outside the caller's scope with the same 404 response as for a resource that does not exist.

#### Scenario: Other user's experiment
- **WHEN** staff user A requests the metrics of an experiment owned by staff user B
- **THEN** the response is 404

#### Scenario: Other user's dataset in experiment creation
- **WHEN** staff user A creates an experiment for a dataset owned by B
- **THEN** the response is 404 and nothing is stored

### Requirement: Derived resources inherit access
The system SHALL apply the access rules of an experiment to its results, evaluations, summary, comparison, per-case view and exports, and the access rules of a dataset to its test cases.

#### Scenario: Export of another user's experiment
- **WHEN** a staff user exports an experiment they do not own
- **THEN** the response is 404 and no data is streamed

### Requirement: Creation needs an organization
The system SHALL allow only users with an organization to create datasets and experiments, and SHALL answer a superadmin attempt with 403 and a message that a superadmin cannot own data.

#### Scenario: Superadmin creates dataset
- **WHEN** a superadmin creates a dataset
- **THEN** the response is 403

### Requirement: Existing data
The system SHALL keep all datasets and experiments that existed before this capability, assigning them to a default organization without an owner, visible to that organization's admins and to superadmins and not to staff.

#### Scenario: Legacy dataset
- **WHEN** a staff user lists datasets after the upgrade
- **THEN** pre-existing datasets are not listed, while an admin of the default organization sees them

### Requirement: Access decisions are logged without content
The system SHALL log denied authentication attempts by reason category and user id when known, and SHALL NOT log tokens, passwords, emails of unknown accounts, or resource content.

#### Scenario: Failed login log
- **WHEN** a login fails
- **THEN** the log records a failure category but not the submitted password or token
