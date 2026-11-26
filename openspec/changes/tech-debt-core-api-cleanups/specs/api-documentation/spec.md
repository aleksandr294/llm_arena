# Spec Delta

## ADDED Requirements

### Requirement: Documentation is off unless enabled
The system SHALL leave Swagger UI, ReDoc and the OpenAPI schema disabled (404) when the documentation setting is not set, and SHALL enable them only when it is explicitly turned on.

#### Scenario: Default configuration
- **WHEN** the application starts without the documentation setting
- **THEN** `/docs`, `/redoc` and `/openapi.json` answer 404

#### Scenario: Explicitly enabled
- **WHEN** the documentation setting is true
- **THEN** all three endpoints are served
