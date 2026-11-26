# admin-panel Specification

## Purpose

Defines the administration panel through which staff, organization admins and superadmins browse the platform's data under the same visibility rules as the REST API, and through which superadmins block and unblock organizations.

## Requirements

### Requirement: Optional admin panel
The system SHALL serve the admin panel under the `/admin` prefix only when an admin secret of at least 32 characters, different from the REST token secret, is configured; otherwise the panel SHALL NOT be mounted and `/admin` SHALL answer 404.

#### Scenario: Not configured
- **WHEN** no admin secret is configured
- **THEN** the application starts normally and `/admin` answers 404

#### Scenario: Weak admin secret
- **WHEN** the admin secret is shorter than 32 characters or equals the REST token secret
- **THEN** startup fails with an error naming the setting and not echoing the value

### Requirement: Panel sign-in
The system SHALL let a user sign in to the panel with the same email and password as the REST API, SHALL reject unknown users, wrong passwords, deactivated users and users of deactivated organizations with the same generic failure, and SHALL keep the panel session in an HttpOnly cookie with SameSite lax that is Secure unless explicitly relaxed for local development.

#### Scenario: Staff signs in
- **WHEN** an active staff user signs in with the correct credentials
- **THEN** the panel opens and the user sees only their own data

#### Scenario: Deactivated user
- **WHEN** a deactivated user signs in
- **THEN** the sign-in fails with the same generic message as a wrong password

### Requirement: Session follows user state
The system SHALL treat a panel session as invalid as soon as its user or the user's organization is deactivated, answering 401 without waiting for the session to expire.

#### Scenario: Blocked organization
- **WHEN** a superadmin blocks an organization and one of its users then calls the panel with an existing session
- **THEN** the response is 401

### Requirement: Role-based visibility in the panel
The system SHALL show in the panel only the datasets, test cases, experiments, experiment results and evaluation results the user could read through the REST API: staff their own, admin those of their organization, superadmin all. Test cases belong to the scope of their dataset, and results and evaluations to that of their experiment.

#### Scenario: Staff isolation
- **WHEN** staff user A opens the experiment list while staff user B in the same organization owns experiments
- **THEN** only A's experiments are listed and B's experiment ids answer not found

#### Scenario: Admin scope
- **WHEN** an admin opens the dataset list
- **THEN** datasets of the admin's organization are listed and none from other organizations

#### Scenario: Superadmin scope
- **WHEN** a superadmin opens any list
- **THEN** rows of every organization are included

### Requirement: Users and organizations in the panel
The system SHALL show users to admins for their own organization, to staff for themselves only and to superadmins for all users, SHALL show organizations to superadmins only, and SHALL never show or return password hashes.

#### Scenario: Admin sees organization users
- **WHEN** an admin opens the users list
- **THEN** only users of the admin's organization are listed

#### Scenario: Hash hidden
- **WHEN** any user record is displayed or fetched through the panel
- **THEN** the password hash is not included

#### Scenario: Organizations for non-superadmins
- **WHEN** an admin or staff user opens the organizations list
- **THEN** it is empty

### Requirement: Fail closed
The system SHALL return no rows and allow no change for any admin request whose authenticated user cannot be resolved to an active user of an active organization.

#### Scenario: Unresolved user
- **WHEN** a request reaches a panel query without a resolved user
- **THEN** the query returns no rows

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

### Requirement: Block and unblock organizations
The system SHALL let a superadmin block and unblock an organization from the panel, changing only its active state, SHALL apply the same effects as the REST deactivation (users cannot sign in and their refresh tokens are revoked), and SHALL forbid every other user from changing organizations.

#### Scenario: Block
- **WHEN** a superadmin blocks an organization in the panel
- **THEN** its users cannot sign in to the REST API or the panel and their refresh tokens no longer work

#### Scenario: Unblock
- **WHEN** a superadmin unblocks the organization
- **THEN** its users can sign in again

#### Scenario: Other fields untouched
- **WHEN** a superadmin submits an organization change that includes a different name
- **THEN** only the active state is applied

#### Scenario: Admin cannot block
- **WHEN** an admin sends a change request for an organization to the panel
- **THEN** the response is 403

### Requirement: Panel hides bulky and sensitive columns
The system SHALL omit password hashes entirely and SHALL not load prompt, response and test case text into list views, showing them only on the detail view of a row the user may read.

#### Scenario: List view payload
- **WHEN** a user lists experiment results
- **THEN** the list rows contain no response text or rendered prompt

### Requirement: Panel data is untrusted
The system SHALL treat stored datasets, prompts and model output as data to display and SHALL NOT write passwords, session cookies, tokens, hashes or row content to logs.

#### Scenario: Log content
- **WHEN** users sign in, fail to sign in and browse the panel
- **THEN** the logs contain no passwords, cookies, hashes or row content

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
