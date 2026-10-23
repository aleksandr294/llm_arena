# Spec Delta

## Purpose

Defines the automated and manual security checks the project must pass, so vulnerabilities in the code, the dependencies and the running application are found before release and every finding has a recorded outcome.

## ADDED Requirements

### Requirement: Static security analysis
The project SHALL run Bandit with a committed configuration over the production code (application, admin, API, tasks, CLI, entry points and migrations), SHALL report no unresolved finding at medium severity or higher, and SHALL allow a suppression only as an inline comment that states the reason.

#### Scenario: Clean scan
- **WHEN** the documented Bandit command is run on a clean checkout
- **THEN** it exits successfully with no unresolved medium or high findings

#### Scenario: Unjustified suppression
- **WHEN** a production file contains a Bandit suppression without a reason comment
- **THEN** the project's automated check fails

### Requirement: Scan runs before changes land
The project SHALL run the static analysis as a pre-commit hook next to the existing formatters and type checker, using the pinned tool version.

#### Scenario: Hook configured
- **WHEN** the pre-commit configuration is inspected
- **THEN** it contains a Bandit hook that uses the project configuration

### Requirement: Dependency vulnerability audit
The project SHALL provide a documented command that audits the locked dependencies against a public vulnerability database and SHALL record, for every reported vulnerability, whether it was fixed by upgrading or accepted with a reason.

#### Scenario: Audit result recorded
- **WHEN** the audit reports a vulnerable dependency
- **THEN** the findings register lists it as fixed or accepted with the reason

### Requirement: Dynamic application scan
The project SHALL document and run an OWASP ZAP baseline scan and an OpenAPI-driven scan against a locally started instance using throwaway data and credentials, and SHALL record every alert of medium risk or higher as fixed or accepted with a reason.

#### Scenario: Scan against a throwaway instance
- **WHEN** the dynamic scan is run
- **THEN** it targets a locally started instance backed by a scratch database and never the developer's real stack

### Requirement: Findings register
The project SHALL keep a findings register in `docs/security.md` listing each finding from the static analysis, dependency audit, dynamic scan, OWASP review and code review with its source, severity, location, and outcome (fixed with reference, or accepted with reason and owner).

#### Scenario: Every finding has an outcome
- **WHEN** the register is reviewed
- **THEN** no entry lacks an outcome

### Requirement: OWASP review coverage
The project SHALL document, for each item of the OWASP Top 10 and the OWASP API Security Top 10, how the application addresses it or why it does not apply, with a pointer to code or tests.

#### Scenario: Complete checklist
- **WHEN** the OWASP review table is read
- **THEN** every item of both lists has an entry
