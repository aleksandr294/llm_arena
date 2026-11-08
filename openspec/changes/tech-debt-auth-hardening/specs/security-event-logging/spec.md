# Spec Delta

## Purpose

Gives operators a structured trail of security-relevant outcomes (failed logins, lockouts, refresh reuse, permission denials, admin panel sign-in) without recording secrets or request content.

## ADDED Requirements

### Requirement: Security events are logged
The system SHALL emit one structured log event, with a stable event name, outcome, user id when known, and the client address and request id when available (the project has no request id yet, and admin panel events carry neither address nor request id), for each failed login, lockout, refresh token reuse, permission denial and admin panel sign-in.

#### Scenario: Failed login
- **WHEN** a login fails
- **THEN** one event named `auth.login_failed` is logged with the client address and no password, token or email

#### Scenario: Refresh reuse
- **WHEN** a used refresh token is presented again
- **THEN** one event named `auth.refresh_reuse` is logged with the user id

### Requirement: Security events carry no secrets
The system SHALL NOT include passwords, password hashes, tokens, the JWT secret, emails or request bodies in security events, including hostile input.

#### Scenario: Hostile identifier
- **WHEN** a login is attempted with an email containing newlines and control characters
- **THEN** the event contains no raw email and no unescaped control characters
