# Spec Delta

## Purpose

Defines the automated checks that run for every pull request, which of them block a merge, and how their results are made available to reviewers.

## ADDED Requirements

### Requirement: Pull-request workflow
The project SHALL run a GitHub Actions workflow on every pull request targeting `main` and on manual dispatch, and SHALL NOT build or publish images or deploy from it.

#### Scenario: Pull request opened
- **WHEN** a pull request to `main` is opened or updated
- **THEN** the test, static security, dependency audit and dynamic scan jobs run

#### Scenario: Push to a feature branch only
- **WHEN** commits are pushed to a branch with no pull request
- **THEN** the workflow does not run

### Requirement: Tests and quality gates
The workflow SHALL run Ruff lint, Black check, mypy and the full pytest suite, including the integration tests, against throwaway PostgreSQL and RabbitMQ instances whose database name contains `test`, and the job SHALL fail when any of them fails.

#### Scenario: Failing test
- **WHEN** a pull request breaks a test
- **THEN** the tests job fails and reports the failing test

#### Scenario: Integration tests use a scratch database
- **WHEN** the tests job runs
- **THEN** it never uses a database whose name lacks `test`

### Requirement: Static security analysis in CI
The workflow SHALL run Ruff's security rules (the Bandit rule family) over the production code and SHALL fail on any finding that has no justified inline suppression.

#### Scenario: New unsafe call
- **WHEN** a pull request adds a call flagged by a security rule without a suppression
- **THEN** the job fails and names the rule and location

### Requirement: Dependency audit in CI
The workflow SHALL audit the locked runtime dependencies with `pip-audit` and SHALL fail on any known vulnerability unless its advisory id is ignored explicitly in the workflow with the reason recorded in the findings register.

#### Scenario: Vulnerable runtime dependency
- **WHEN** a locked runtime dependency has a known vulnerability
- **THEN** the audit job fails and lists the package and advisory

#### Scenario: Audit database unreachable
- **WHEN** the vulnerability database cannot be reached
- **THEN** the job fails instead of passing

### Requirement: Dynamic scan in CI
The workflow SHALL start a throwaway instance from the repository's image with scratch credentials, run the OWASP ZAP baseline and OpenAPI scans against it, and publish the reports as a workflow artifact without failing the pull request on findings.

#### Scenario: Reports published
- **WHEN** the scan job finishes
- **THEN** the HTML and JSON reports are attached to the run and contain no token or credential

#### Scenario: Scan setup fails
- **WHEN** the instance does not become healthy
- **THEN** the job fails, so a missing scan is never reported as a clean one

### Requirement: Least privilege and no secrets in logs
The workflow SHALL request read-only repository permissions, SHALL pin third-party actions to a full version, SHALL use only throwaway generated credentials and SHALL NOT print them.

#### Scenario: Token permissions
- **WHEN** the workflow file is inspected
- **THEN** it declares `permissions: contents: read` and no job widens it
