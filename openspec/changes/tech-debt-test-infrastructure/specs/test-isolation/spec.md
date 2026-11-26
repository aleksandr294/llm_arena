# Spec Delta

## Purpose

Keeps the automated test suite reproducible: the same code gives the same result on any machine, whatever the developer's local `.env` or timing, and test categories are selected by reliable markers.

## ADDED Requirements

### Requirement: Tests ignore the developer environment file
The test suite SHALL build every `SettingsSchema` without reading a `.env` file, and SHALL fail a guard test when a test module builds it any other way.

#### Scenario: Local .env present
- **WHEN** a developer's `.env` sets values that differ from the defaults and the suite runs
- **THEN** no test result depends on those values

#### Scenario: New test builds settings directly
- **WHEN** a test constructs `SettingsSchema` without disabling the env file
- **THEN** the guard test fails and names the file and line

### Requirement: Integration tests are marked automatically
The test suite SHALL mark every test that uses a database or broker fixture as `integration` at collection time, and SHALL reject unknown markers.

#### Scenario: Selecting unit tests
- **WHEN** the suite runs with `-m "not integration"` and no database or broker is available
- **THEN** it completes without connection errors

#### Scenario: Misspelled marker
- **WHEN** a test uses an unregistered marker
- **THEN** collection fails

### Requirement: Failures name their tests
The test suite SHALL print the names of failed and errored tests in the run summary.

#### Scenario: Setup error
- **WHEN** a fixture raises during setup
- **THEN** the summary lists that test as an error

### Requirement: Boundary tests assert both sides
Tests named for an inclusive limit or an idempotent operation SHALL assert the observable result, and a boundary test SHALL also assert that the first value beyond the limit is rejected, with an expected-exception check that names a specific exception type.

#### Scenario: Boundary test
- **WHEN** a test is named for an inclusive boundary
- **THEN** it asserts the boundary value is accepted and the next value is rejected

#### Scenario: Idempotent logout
- **WHEN** a test checks that logout is idempotent
- **THEN** it asserts the second call succeeds and the token is still unusable

### Requirement: Tests do not depend on wall-clock timing
Concurrency and timeout tests SHALL synchronize with events or bounded deadlines instead of fixed sleeps and fixed-length hangs.

#### Scenario: Readiness hang
- **WHEN** the health test needs a dependency that never answers
- **THEN** it waits on an event that is never set and the test finishes within the configured timeout
