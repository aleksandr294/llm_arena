# api-documentation Specification

## Purpose

Defines the interactive REST API documentation so developers can discover every endpoint, understand authentication and roles, and try authenticated requests from the browser.

## Requirements

### Requirement: Interactive documentation
The system SHALL serve Swagger UI at `/docs`, ReDoc at `/redoc` and the OpenAPI schema at `/openapi.json`, enabled by default.

#### Scenario: Docs available
- **WHEN** a client opens `/docs`, `/redoc` and `/openapi.json`
- **THEN** each answers successfully

### Requirement: Documentation switch
The system SHALL allow all three documentation endpoints to be turned off with a setting, and when off SHALL answer 404 for them while all other endpoints are unaffected.

#### Scenario: Docs disabled
- **WHEN** the documentation setting is off
- **THEN** `/docs`, `/redoc` and `/openapi.json` answer 404 and the API keeps working

### Requirement: API description and structure
The system SHALL document the API with a title, version and a description that explains authentication, roles and visibility, SHALL group endpoints by tags with descriptions (for example authentication, organizations, users, datasets, experiments, evaluations, metrics and export, health), and SHALL give every operation a summary.

#### Scenario: Every operation described
- **WHEN** the OpenAPI schema is generated
- **THEN** every operation has at least one tag and a summary

### Requirement: Authentication in the documentation
The system SHALL declare the Bearer token scheme so that Swagger UI offers an Authorize button, SHALL mark every protected operation as requiring it and the public operations (health, login, refresh, logout, validate) as not requiring it, and SHALL keep a pasted token across page reloads in Swagger UI.

#### Scenario: Security declared
- **WHEN** the OpenAPI schema is generated
- **THEN** all operations except the public ones declare the Bearer requirement

### Requirement: Documented errors
The system SHALL document, for protected operations, the 401 response, for operations restricted by role the 403 response, for operations on a single resource the 404 response, for state-changing operations that can conflict the 409 response, and the 422 validation response, with a consistent error body shape.

#### Scenario: Error responses listed
- **WHEN** a client reads the schema of an experiment action endpoint
- **THEN** it lists 401, 404 and 409 responses with the error body schema

### Requirement: Documentation excludes the admin panel
The system SHALL NOT include the admin panel's routes in the OpenAPI schema.

#### Scenario: Schema content
- **WHEN** the admin panel is mounted and the schema is generated
- **THEN** no path under the admin prefix appears
