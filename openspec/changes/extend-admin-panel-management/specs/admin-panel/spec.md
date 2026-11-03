## MODIFIED Requirements

### Requirement: Read-only data
The system SHALL NOT allow creating, editing, deleting or exporting datasets, test cases, experiment results, evaluation results or users through the panel, except the operations granted by the requirements for organizations, users, refresh tokens and experiments, SHALL reject every other such request server-side with 403, and SHALL not offer the actions in the interface.

#### Scenario: Crafted delete
- **WHEN** a user sends a delete request for a dataset directly to the panel API
- **THEN** the response is 403 and the dataset still exists

#### Scenario: Export disabled
- **WHEN** a user requests an export of a list through the panel
- **THEN** the response is 403

#### Scenario: Crafted edit of a protected field
- **WHEN** an admin submits a user change that includes a different role or email
- **THEN** only the active state is applied

## ADDED Requirements

### Requirement: Block and unblock users
The system SHALL let an admin block and unblock users of their own organization, and a superadmin any non-superadmin user, from the panel, changing only the active state, with the effects of the REST deactivation (the user cannot sign in, existing refresh tokens are revoked, existing panel sessions answer 401).

#### Scenario: Admin blocks a staff user
- **WHEN** an admin blocks a staff user of their organization
- **THEN** that user can no longer sign in to the REST API or the panel and their refresh tokens no longer work

#### Scenario: Unblock
- **WHEN** the admin unblocks the user
- **THEN** the user can sign in again with the existing password

#### Scenario: Forbidden targets
- **WHEN** a user tries to block a superadmin, themselves, a user of another organization, or any user as staff
- **THEN** the change is refused and the target stays active

### Requirement: Create organizations and users
The system SHALL let a superadmin create organizations, and an admin (own organization) or superadmin create `admin` and `staff` users, applying the same validation as the REST API (unique names and emails ignoring case, password policy), and SHALL NOT create superadmins or users in another organization for an admin.

#### Scenario: Superadmin creates an organization
- **WHEN** a superadmin submits a new organization name
- **THEN** the organization exists and is active

#### Scenario: Admin creates a staff user
- **WHEN** an admin creates a user with a valid email, password and role `staff`
- **THEN** the user belongs to the admin's organization and can sign in with that password

#### Scenario: Rejected creations
- **WHEN** the email or organization name already exists, the password is too weak, the role is `superadmin`, or an admin names another organization
- **THEN** nothing is created and the response is an error that does not echo the password

#### Scenario: Staff cannot create
- **WHEN** a staff user sends a create request for a user or organization
- **THEN** the response is 403

### Requirement: Revoke refresh tokens
The system SHALL let an admin revoke all refresh tokens of users of their own organization, and a superadmin of any user, from the panel, without changing the user's active state or password.

#### Scenario: Revoke
- **WHEN** an admin revokes the refresh tokens of a staff user of their organization
- **THEN** that user's refresh tokens no longer work and the user can sign in again

#### Scenario: Out of scope
- **WHEN** an admin selects a user of another organization
- **THEN** that user is not affected and the response does not reveal whether the user exists

### Requirement: Delete finished experiments
The system SHALL let an admin delete experiments of their organization, and a superadmin any experiment, from the panel only when the experiment is in a terminal state, deleting its results and evaluation results with it and leaving its dataset and test cases untouched.

#### Scenario: Delete a completed experiment
- **WHEN** an admin deletes a completed experiment of their organization
- **THEN** the experiment, its results and its evaluation results are gone and its dataset remains

#### Scenario: Active experiment
- **WHEN** the experiment is pending or running
- **THEN** the delete is refused with a message to cancel it first and nothing is removed

#### Scenario: Out of scope or staff
- **WHEN** staff delete an experiment, or an admin deletes one of another organization
- **THEN** the response is 403 or not found respectively and the experiment still exists

### Requirement: Detailed views
The system SHALL show on the detail view of an experiment its dataset, owner and counts of results by status; of an experiment result its experiment and test case position; of a user their organization and number of active refresh tokens; of an organization its user count; of a dataset its test case count; and of a test case its dataset. The views SHALL respect the visibility rules and SHALL NOT include password hashes, tokens or token hashes.

#### Scenario: Experiment detail
- **WHEN** a user opens an experiment they may read
- **THEN** the detail shows the dataset name and a count of results for each status

#### Scenario: Related data out of scope
- **WHEN** a related row is not visible to the user
- **THEN** it is omitted or shown as unavailable without revealing its content

#### Scenario: No secrets
- **WHEN** a user detail is displayed
- **THEN** it shows the number of active refresh tokens and no token or hash value
