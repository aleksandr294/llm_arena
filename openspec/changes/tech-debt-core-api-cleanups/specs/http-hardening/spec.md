# Spec Delta

## ADDED Requirements

### Requirement: Documentation sources are pinned to paths
The Content Security Policy of the documentation pages SHALL allow scripts and stylesheets from the content delivery network only under the exact package paths those pages load, not from the whole origin.

#### Scenario: Other package on the same origin
- **WHEN** the policy of `/docs` is inspected
- **THEN** it lists the Swagger UI package path and not the bare origin

### Requirement: Cross-origin and permission headers
The system SHALL add `Permissions-Policy` denying camera, microphone, geolocation and payment, `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Resource-Policy: same-origin` to every HTTP response.

#### Scenario: Headers present
- **WHEN** a client calls any endpoint, including error and 413 responses
- **THEN** the response carries the three headers

#### Scenario: Pages still work
- **WHEN** a client opens the Swagger UI and the admin panel
- **THEN** both load and function

### Requirement: Connection close only on HTTP/1
The system SHALL send `Connection: close` on a 413 response only for HTTP/1.x requests.

#### Scenario: HTTP/2 request
- **WHEN** an oversized request arrives over HTTP/2
- **THEN** the 413 response has no Connection header
