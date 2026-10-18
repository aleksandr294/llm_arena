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
The system SHALL NOT allow creating, editing, deleting or exporting datasets, test cases, experiments, experiment results, evaluation results or users through the panel, SHALL reject such requests server-side with 403, and SHALL not offer the actions in the interface.

#### Scenario: Crafted delete
- **WHEN** a user sends a delete request for a dataset directly to the panel API
- **THEN** the response is 403 and the dataset still exists

#### Scenario: Export disabled
- **WHEN** a user requests an export of a list through the panel
- **THEN** the response is 403

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
