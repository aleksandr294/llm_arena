# Spec Delta

## Purpose

Defines organizations, users and their roles, who may manage whom, and how the first administrator is created, so that each organization has its own users and the platform has a controlled hierarchy.

## ADDED Requirements

### Requirement: Roles
The system SHALL support three roles: staff, admin and superadmin. A staff or admin user SHALL belong to exactly one organization, and a superadmin SHALL belong to none.

#### Scenario: Role organization rule
- **WHEN** a staff or admin user is created without an organization, or a superadmin is created with one
- **THEN** the request is rejected with 422

### Requirement: Manage organizations
The system SHALL let only a superadmin create, list, read and update organizations (name and active state), SHALL require unique organization names ignoring case, and SHALL not delete organizations.

#### Scenario: Create organization
- **WHEN** a superadmin creates an organization named "Acme"
- **THEN** the response is 201 and the organization appears in the list

#### Scenario: Duplicate name
- **WHEN** a superadmin creates an organization whose name differs from an existing one only by case
- **THEN** the response is 409

#### Scenario: Forbidden for others
- **WHEN** an admin or staff user calls an organization endpoint
- **THEN** the response is 403

### Requirement: Create users
The system SHALL let an admin create staff and admin users in their own organization, and a superadmin create staff and admin users in any organization. Email SHALL be unique ignoring case, valid and at most 254 characters, and the password at least 12 and at most 128 characters without NUL.

#### Scenario: Admin creates staff
- **WHEN** an admin creates a staff user
- **THEN** the response is 201 and the user belongs to the admin's organization

#### Scenario: Admin outside own organization
- **WHEN** an admin tries to create a user in another organization
- **THEN** the response is 403

#### Scenario: Privilege escalation
- **WHEN** an admin tries to create a superadmin
- **THEN** the response is 403

#### Scenario: Weak password
- **WHEN** a user is created with an 8-character password
- **THEN** the response is 422 naming the password field

#### Scenario: Duplicate email
- **WHEN** a user is created with an email that already exists in any case
- **THEN** the response is 409

### Requirement: Read users
The system SHALL let an admin list and read users of their own organization and a superadmin list and read all users with optional filters by organization, role and active state, with pagination (limit 1 to 100, default 50, offset 0 or more) and a total. Staff SHALL be able to read only themselves, and users outside the caller's scope SHALL respond as not found. Responses SHALL never contain password hashes.

#### Scenario: Admin scope
- **WHEN** an admin lists users
- **THEN** only users of the admin's organization are returned

#### Scenario: Staff scope
- **WHEN** a staff user lists users
- **THEN** only the staff user themselves is returned

### Requirement: Update users
The system SHALL let an admin update role (staff or admin), active state and password of users in their organization, and a superadmin do so for any non-superadmin user, SHALL forbid changing one's own role or deactivating oneself, and SHALL end the user's sessions when they are deactivated or their password is changed.

#### Scenario: Deactivate user
- **WHEN** an admin deactivates a staff user
- **THEN** the user's access and refresh tokens stop working

#### Scenario: Self lockout
- **WHEN** an admin tries to deactivate themselves
- **THEN** the response is 409

### Requirement: Deactivated organization
The system SHALL treat all users of a deactivated organization as unable to authenticate, without deleting their data.

#### Scenario: Organization deactivated
- **WHEN** a superadmin deactivates an organization
- **THEN** its users receive 401 on their next request

### Requirement: Bootstrap the first superadmin
The system SHALL provide a command-line action that creates a superadmin from an email and a password read interactively or from the environment, SHALL refuse a duplicate email, and SHALL NOT expose any unauthenticated endpoint that creates users.

#### Scenario: Bootstrap
- **WHEN** the operator runs the create-superadmin command with a valid email and password
- **THEN** the superadmin can log in

#### Scenario: No open registration
- **WHEN** an unauthenticated client calls the user creation endpoint
- **THEN** the response is 401
