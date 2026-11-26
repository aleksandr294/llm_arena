## MODIFIED Requirements

### Requirement: Dynamic application scan
The project SHALL document and run an OWASP ZAP baseline scan and an OpenAPI-driven scan against a locally started instance using throwaway data and credentials, SHALL run a sqlmap SQL injection scan of the API in the pull-request workflow, and SHALL record every alert of medium risk or higher, and every confirmed injection, as fixed or accepted with a reason.

#### Scenario: Scan against a throwaway instance
- **WHEN** the dynamic scan is run
- **THEN** it targets a locally started instance backed by a scratch database and never the developer's real stack

#### Scenario: Injection finding recorded
- **WHEN** the sqlmap scan confirms an injectable parameter
- **THEN** the findings register lists it as fixed with a reference, or accepted with a reason and an owner
