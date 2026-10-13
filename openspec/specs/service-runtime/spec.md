# service-runtime Specification

## Purpose

Defines how the backend starts, loads configuration, protects secrets, and exposes versioned HTTP endpoints including health checks.

## Requirements

### Requirement: Environment-based configuration
The system SHALL load all settings from environment variables, optionally from a local `.env` file, and SHALL fail at startup with an error naming the invalid or missing setting.

#### Scenario: Missing required setting
- **WHEN** the application starts without a required setting such as the database URL
- **THEN** startup fails and the error names the missing setting

#### Scenario: Example configuration provided
- **WHEN** a developer inspects the repository
- **THEN** `.env.example` lists every setting with placeholder values and no real credentials

### Requirement: Secrets are not exposed
The system MUST NOT write secret values (API keys, passwords, broker or database credentials) to logs or API responses.

#### Scenario: Settings rendered for diagnostics
- **WHEN** settings are logged or serialized
- **THEN** secret fields are masked

### Requirement: Versioned API routing
The system SHALL serve all business endpoints under the `/api/v1` prefix.

#### Scenario: Unknown version
- **WHEN** a client requests `/api/v2/anything`
- **THEN** the system responds with HTTP 404

### Requirement: Liveness endpoint
The system SHALL expose a liveness endpoint that returns HTTP 200 while the process is running, without checking external dependencies.

#### Scenario: Dependencies down
- **WHEN** PostgreSQL is unreachable and the liveness endpoint is requested
- **THEN** the system responds with HTTP 200

### Requirement: Readiness endpoint
The system SHALL expose a readiness endpoint that reports the status of PostgreSQL and RabbitMQ and returns HTTP 503 if any is unavailable.

#### Scenario: All dependencies healthy
- **WHEN** the readiness endpoint is requested and both dependencies respond
- **THEN** the system responds with HTTP 200 and per-dependency status `ok`

#### Scenario: Dependency unavailable
- **WHEN** the readiness endpoint is requested and PostgreSQL is unreachable
- **THEN** the system responds with HTTP 503 and reports the database as failing

### Requirement: Runnable entry point
The system SHALL start the API server via `python main.py API`.

#### Scenario: Start API
- **WHEN** a developer runs `python main.py API` with valid configuration
- **THEN** the HTTP server listens on the configured host and port
