# http-hardening Specification

## Purpose

Defines the baseline HTTP and container protections the platform applies on top of authentication and access control, closing the gaps that standard web security scans report.

## Requirements

### Requirement: Security response headers
The system SHALL add `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer` to every HTTP response, and SHALL add `Strict-Transport-Security` only when enabled by configuration for HTTPS deployments.

#### Scenario: Headers present
- **WHEN** a client calls any endpoint, including error responses
- **THEN** the response carries the three headers

#### Scenario: HSTS off by default
- **WHEN** HSTS is not enabled in configuration
- **THEN** responses carry no Strict-Transport-Security header

### Requirement: Content Security Policy
The system SHALL add a `Content-Security-Policy` header to every HTTP response, and responses that are not HTML documents SHALL carry the strict policy `default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`.

#### Scenario: API responses
- **WHEN** a client calls any API endpoint, including error and 413 responses
- **THEN** the response carries the strict policy

### Requirement: Page policies for documentation and panel
The HTML pages served by the application (Swagger UI, ReDoc and the admin panel) SHALL carry the tightest policy that lets them work, allowing inline scripts only by SHA-256 hash of their exact content and never `'unsafe-inline'` or `'unsafe-eval'` for scripts.

#### Scenario: Documentation and panel pages
- **WHEN** a client opens `/docs`, `/redoc` or `/admin/`
- **THEN** the policy allows every script and stylesheet origin and every inline script present in that page, and no other inline script

### Requirement: Sensitive responses are not cached
The system SHALL send `Cache-Control: no-store` on authentication responses (login, refresh, validate, and the admin panel sign-in and sign-out) and on every response to a request carrying an Authorization header or the admin session cookie.

#### Scenario: Token responses
- **WHEN** a client logs in
- **THEN** the response has `Cache-Control: no-store`

### Requirement: Request size limit
The system SHALL reject a request whose body exceeds a configurable maximum (default 2 MiB) with 413 before reading the whole body, for requests that declare a length and for chunked bodies.

#### Scenario: Oversized body
- **WHEN** a client posts a body larger than the limit
- **THEN** the response is 413 and nothing is stored

#### Scenario: Normal body
- **WHEN** a client posts a dataset case within the existing field limits
- **THEN** the request is accepted

### Requirement: Hardening does not break documentation and the panel
The system SHALL apply the headers and limits without breaking the interactive API documentation or the admin panel pages.

#### Scenario: Docs still work
- **WHEN** a client opens the Swagger UI and the admin panel
- **THEN** both load and function

### Requirement: Container runs unprivileged
The container image SHALL run the application as a non-root user, and the Compose file SHALL publish the database and message broker ports only on the loopback interface.

#### Scenario: Image user
- **WHEN** the built image is started and its user is inspected
- **THEN** the process does not run as root

#### Scenario: Published ports
- **WHEN** the Compose file is inspected
- **THEN** the PostgreSQL and RabbitMQ ports are bound to 127.0.0.1
