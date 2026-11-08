# Spec Delta

## ADDED Requirements

### Requirement: Foreign keys are indexed
The schema SHALL have an index on every foreign key column that is used to look up or delete referencing rows, including `experiments.dataset_id` and `experiment_results.test_case_id`.

#### Scenario: Dataset lookup from experiments
- **WHEN** the schema is inspected after all migrations
- **THEN** `experiments.dataset_id` and `experiment_results.test_case_id` each have an index

### Requirement: Migrations round trip with one head
The migrations SHALL apply from an empty database to head, SHALL downgrade back to base, and SHALL have exactly one head revision; the schema at head SHALL match the models, including column types.

#### Scenario: Round trip
- **WHEN** the migrations are upgraded to head and downgraded to base on an empty test database
- **THEN** both steps succeed and no tables of the application remain

#### Scenario: Two heads
- **WHEN** two migration files declare the same parent
- **THEN** the single-head test fails and names both revisions

#### Scenario: Model drift
- **WHEN** a model column type differs from the migrated schema
- **THEN** the drift check reports it
