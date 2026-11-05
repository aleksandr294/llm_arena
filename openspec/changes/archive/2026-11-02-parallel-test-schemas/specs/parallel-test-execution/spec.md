# Spec Delta

## Purpose

Lets the test suite run in parallel groups that cannot interfere with each other, by giving each group its own database schema, while a plain serial run keeps working unchanged.

## ADDED Requirements

### Requirement: Schema per parallel test group
The test suite SHALL run each parallel group of tests against its own PostgreSQL schema in the test database, so tables created, filled and dropped by one group are never visible to another.

#### Scenario: Two groups run at once
- **WHEN** the suite runs with two or more parallel workers
- **THEN** each worker creates its tables in a schema named for that worker, and no worker reads, writes or drops another worker's tables

#### Scenario: Unqualified table names
- **WHEN** a test opens its own engine from the configured database URL
- **THEN** its unqualified table names resolve to its worker's schema only, not to `public`

#### Scenario: Panel child process
- **WHEN** a test starts the admin panel child process
- **THEN** the child uses the same schema as the test that started it

### Requirement: Schema lifecycle
Each parallel group SHALL create its schema before its first test and remove it, with its contents, after its last test.

#### Scenario: Normal run
- **WHEN** a worker finishes its tests
- **THEN** its schema no longer exists in the test database

#### Scenario: Leftover schema from an aborted run
- **WHEN** a worker starts and a schema with its name already exists
- **THEN** the schema is recreated empty before any test runs

### Requirement: Destructive-test guard applies to schemas
Creating or dropping a worker schema SHALL happen only on a database whose name contains `test`.

#### Scenario: Non-test database
- **WHEN** `DATABASE_URL` names a database without `test` in its name
- **THEN** no schema is created or dropped and the tests fail with the existing guard message

### Requirement: Serial run unchanged
A run without parallel workers SHALL keep using the database's default schema and require no schema setup.

#### Scenario: Plain pytest
- **WHEN** the suite runs with plain `pytest` and no xdist workers
- **THEN** tests create and drop their tables in the default schema as before
