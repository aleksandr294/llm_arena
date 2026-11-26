## MODIFIED Requirements

### Requirement: Static security analysis
The project SHALL run Ruff's security rules (the Bandit rule family, selected with `S`) with a committed configuration over the production code (application, admin, API, tasks, CLI, entry points and migrations), SHALL report no unresolved finding, and SHALL allow a suppression only as an inline `# noqa: Sxxx - reason` comment that states the reason.

#### Scenario: Clean scan
- **WHEN** the documented Ruff security command is run on a clean checkout
- **THEN** it exits successfully with no unresolved findings

#### Scenario: Unjustified suppression
- **WHEN** a production file contains a security suppression without a reason comment
- **THEN** the project's automated check fails

### Requirement: Scan runs before changes land
The project SHALL run the static analysis as a pre-commit hook next to the existing formatters and type checker, using the pinned tool version, and SHALL run it again in the pull-request workflow.

#### Scenario: Hook configured
- **WHEN** the pre-commit configuration is inspected
- **THEN** its Ruff hook covers the security rules and no Bandit hook remains

### Requirement: Dependency vulnerability audit
The project SHALL provide a documented command that audits the locked dependencies against a public vulnerability database, SHALL run it for the runtime dependencies in the pull-request workflow, and SHALL record, for every reported vulnerability, whether it was fixed by upgrading or accepted with a reason.

#### Scenario: Audit result recorded
- **WHEN** the audit reports a vulnerable dependency
- **THEN** the findings register lists it as fixed or accepted with the reason

### Requirement: Dynamic application scan
The project SHALL document and run an OWASP ZAP baseline scan and an OpenAPI-driven scan against a locally started instance using throwaway data and credentials, including as a report-only job in the pull-request workflow, and SHALL record every alert of medium risk or higher as fixed or accepted with a reason.

#### Scenario: Scan against a throwaway instance
- **WHEN** the dynamic scan is run
- **THEN** it targets a locally started instance backed by a scratch database and never the developer's real stack
