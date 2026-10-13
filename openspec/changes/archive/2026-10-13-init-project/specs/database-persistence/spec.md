# Spec Delta

## Purpose

Defines how the backend connects to PostgreSQL, scopes transactions, and manages schema changes through migrations.

## ADDED Requirements

### Requirement: Transactional sessions
The system SHALL provide database sessions scoped to a unit of work that commit on success and roll back on any error.

#### Scenario: Error inside unit of work
- **WHEN** an exception is raised after a write within a unit of work
- **THEN** the write is rolled back and not visible to later sessions

#### Scenario: Successful unit of work
- **WHEN** a unit of work completes without error
- **THEN** its writes are committed

### Requirement: Shared repository base
The system SHALL provide a base repository that components extend for persistence, keeping queries out of API handlers and services.

#### Scenario: Component repository
- **WHEN** a component defines a repository extending the base
- **THEN** it can create, read, update, and delete its model using the shared session

### Requirement: Migration management
The system SHALL manage schema changes through versioned migrations that can be applied to an empty database and report the current revision.

#### Scenario: Apply to empty database
- **WHEN** migrations are upgraded to head on an empty PostgreSQL database
- **THEN** the command succeeds and the database reports the head revision

#### Scenario: Model metadata discovered
- **WHEN** a component model is imported into the shared metadata
- **THEN** migration autogeneration detects its table
