# Spec Delta

## Purpose

Defines how users prove their identity and keep a session using short-lived JWT access tokens and revocable refresh tokens, so that every protected request can be attributed to an active user without trusting stale or stolen credentials.

## ADDED Requirements

### Requirement: Login
The system SHALL authenticate a user by email and password and, on success, return an access token, a refresh token, the token type `bearer` and the access token lifetime in seconds. Email comparison SHALL be case-insensitive.

#### Scenario: Successful login
- **WHEN** an active user submits the correct email and password
- **THEN** the response is 200 with an access token, a refresh token and the access token lifetime

#### Scenario: Email case
- **WHEN** a user registered as "Ann@Example.com" logs in with "ann@example.com"
- **THEN** the login succeeds

### Requirement: Uniform credential errors
The system SHALL answer a failed login for an unknown email, a wrong password, a deactivated user or a deactivated organization with the same 401 response, and SHALL perform a password verification for unknown emails so that response time does not reveal whether the email exists.

#### Scenario: Wrong password
- **WHEN** a user submits a wrong password
- **THEN** the response is 401 with a generic message and no tokens

#### Scenario: Unknown email
- **WHEN** a client submits an email that is not registered
- **THEN** the response is identical to the wrong password response

### Requirement: Access token
The system SHALL issue signed access tokens that identify the user, carry the issuer, issue time, a unique id, the token type and an expiry 15 minutes after issue by default, and SHALL reject tokens with a missing or past expiry, a wrong issuer, a bad signature, an unexpected algorithm or a type other than access.

#### Scenario: Expired token
- **WHEN** a request presents an expired access token
- **THEN** the response is 401

#### Scenario: Refresh token as access token
- **WHEN** a request presents a refresh token as a bearer token
- **THEN** the response is 401

#### Scenario: Unsigned token
- **WHEN** a request presents a token whose algorithm is none
- **THEN** the response is 401

### Requirement: Token validation endpoint
The system SHALL provide an endpoint that accepts a token and reports whether it is a currently valid access token, returning the user id, organization id, role and expiry when valid, and a reason (expired, invalid or revoked) when not, without failing the request.

#### Scenario: Valid token
- **WHEN** a client validates a fresh access token of an active user
- **THEN** the response is 200 with valid true and the user's id, role and expiry

#### Scenario: Expired token
- **WHEN** a client validates an expired token
- **THEN** the response is 200 with valid false and reason expired

#### Scenario: Deactivated user
- **WHEN** a client validates an unexpired token of a user who has since been deactivated
- **THEN** the response is 200 with valid false

### Requirement: Refresh with rotation
The system SHALL exchange a valid, unused, unexpired refresh token for a new access token and a new refresh token, and SHALL mark the presented refresh token as used so it cannot be used again. Refresh tokens SHALL expire 7 days after issue by default.

#### Scenario: Refresh
- **WHEN** a client presents a valid refresh token
- **THEN** the response contains a new access token and a new refresh token

#### Scenario: Expired refresh token
- **WHEN** a client presents an expired refresh token
- **THEN** the response is 401

### Requirement: Refresh token reuse detection
The system SHALL treat the presentation of an already used refresh token as a possible theft, SHALL revoke every refresh token issued from the same login, and SHALL answer 401.

#### Scenario: Reuse
- **WHEN** a refresh token is used a second time
- **THEN** the response is 401 and the newer refresh token from that login no longer works

### Requirement: Logout
The system SHALL revoke the refresh tokens of the login session identified by a presented refresh token, so that neither it nor its successors can be refreshed, and SHALL answer successfully even if the token is already revoked.

#### Scenario: Logout
- **WHEN** a client logs out with a valid refresh token and then tries to refresh with it
- **THEN** the refresh response is 401

### Requirement: Server-side refresh token storage
The system SHALL store only a hash of each refresh token identifier, never the token itself, and SHALL end refresh tokens of a user immediately when the user or their organization is deactivated.

#### Scenario: Deactivation ends sessions
- **WHEN** an admin deactivates a user and the user then presents a valid refresh token
- **THEN** the response is 401

### Requirement: Token signing configuration
The system SHALL sign tokens with a secret supplied through configuration of at least 32 characters, SHALL refuse to start authenticated endpoints with a missing, too short or placeholder secret, and SHALL NOT log or return the secret, tokens or passwords.

#### Scenario: Weak secret
- **WHEN** the application is configured with a secret shorter than 32 characters
- **THEN** it fails with an error naming the setting, without echoing the value

### Requirement: Password storage
The system SHALL store passwords only as salted, memory-hard hashes with per-user random salts, and SHALL compare hashes in constant time.

#### Scenario: Hash stored
- **WHEN** a user is created with a password
- **THEN** the stored value is a salted hash that does not contain the password and two users with the same password have different hashes
