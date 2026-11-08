# Spec Delta

## ADDED Requirements

### Requirement: Settings are validated at startup
The system SHALL reject at startup a health check timeout that is not positive, access and refresh token lifetimes above their ceilings (one day and 90 days), a database URL that does not use the async PostgreSQL driver, a broker URL that is not amqp or amqps, and a JWT or admin secret with fewer than 12 distinct characters, naming the setting and never echoing a secret.

#### Scenario: Zero timeout
- **WHEN** the health check timeout is 0
- **THEN** startup fails and the error names the setting

#### Scenario: Wrong database driver
- **WHEN** the database URL uses a synchronous driver
- **THEN** startup fails and the error names DATABASE_URL without printing the URL

#### Scenario: Low-entropy secret
- **WHEN** the JWT secret is 40 repetitions of one character
- **THEN** startup fails naming the setting and not the value

### Requirement: Bounded query parameters
The system SHALL reject a list offset above 1,000,000 and an evaluator id filter longer than the maximum evaluator id with 422.

#### Scenario: Huge offset
- **WHEN** a client requests a list with offset 10,000,000
- **THEN** the response is 422

### Requirement: Clean shutdown
The system SHALL release its message broker connection and database connection pool when the application shuts down, and the operator CLI SHALL release its pool before exiting.

#### Scenario: Application shutdown
- **WHEN** the application stops after having published a task
- **THEN** the broker is shut down and the engine is disposed

### Requirement: CLI reports database errors plainly
The operator CLI SHALL answer an unreachable or failing database with a one-line error and exit status 1, without a traceback and without the connection URL.

#### Scenario: Database down
- **WHEN** `create-superadmin` runs while the database is unreachable
- **THEN** it prints a one-line error without the URL and exits with status 1
