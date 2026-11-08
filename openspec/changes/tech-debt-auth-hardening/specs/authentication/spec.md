# Spec Delta

## ADDED Requirements

### Requirement: Access tokens end with the session
The system SHALL reject an access token issued before the user logged out, changed password or was deactivated, and the token validation endpoint SHALL report it as revoked.

#### Scenario: Logout ends access tokens
- **WHEN** a user logs out and then presents an access token issued before the logout
- **THEN** a protected request answers 401 and validation reports valid false with reason revoked

#### Scenario: Password change ends access tokens
- **WHEN** an admin changes a user's password and the user presents an earlier access token
- **THEN** the request answers 401

#### Scenario: New login still works
- **WHEN** the user logs in again after the password change
- **THEN** the new access token is accepted

### Requirement: Clock skew tolerance
The system SHALL accept a token whose expiry or issue time is off by at most a configured number of seconds (default 5, at most 60), and SHALL reject it beyond that.

#### Scenario: Within leeway
- **WHEN** an access token expired 2 seconds ago and the leeway is 5 seconds
- **THEN** the token is accepted

#### Scenario: Beyond leeway
- **WHEN** an access token expired 10 seconds ago and the leeway is 5 seconds
- **THEN** the token is rejected as expired

### Requirement: Refresh token purge
The system SHALL provide an operator command that deletes refresh token records that expired more than a retention period ago, and SHALL keep unexpired records and records inside the retention period.

#### Scenario: Purge
- **WHEN** the operator runs the purge command with records expired long ago and records still valid
- **THEN** only the long-expired records are deleted and the count is printed

### Requirement: Refresh and deactivation do not race
The system SHALL NOT issue a new token pair to a user who was deactivated before the refresh transaction committed.

#### Scenario: Deactivation wins
- **WHEN** a user is deactivated while a refresh of the same user is in flight
- **THEN** the refresh answers 401 and no usable refresh token remains

### Requirement: Login throttling
The system SHALL count failed attempts per normalized account identifier and per client address on login, refresh, validate and admin sign-in, SHALL answer 429 with a `Retry-After` header once the configured number of failures is reached within the window, and SHALL apply the same answer to unknown and known accounts.

#### Scenario: Lockout after repeated failures
- **WHEN** a client submits the configured number of wrong passwords for one email
- **THEN** the next attempt, even with the correct password, answers 429 with `Retry-After`

#### Scenario: Unknown account
- **WHEN** a client fails the same number of times for an email that is not registered
- **THEN** the same 429 is returned

#### Scenario: Window expiry
- **WHEN** the lockout period has passed
- **THEN** a correct login succeeds and the counter is reset

#### Scenario: Cancelled hash does not free capacity early
- **WHEN** a login request is cancelled while its password hash is being computed
- **THEN** the hashing slot stays occupied until the computation ends
