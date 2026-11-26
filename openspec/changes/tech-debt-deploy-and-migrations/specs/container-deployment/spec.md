# Spec Delta

## Purpose

Defines what the Docker Compose stack provides so that `docker compose up --build` yields a working, restart-safe environment whose behavior matches the documented settings.

## ADDED Requirements

### Requirement: Tunable settings reach the containers
The Compose file SHALL pass every operator-tunable setting of the application to the api and worker containers with a default equal to the application default at merge time, so that an unset optional variable never reaches the application as an empty string.

#### Scenario: Optional variables unset
- **WHEN** the stack starts with only the required variables set
- **THEN** the api and worker start with the application defaults and no setting fails validation

#### Scenario: Provider and pricing settings set
- **WHEN** the operator sets the provider keys, the pricing file and the concurrency limit in `.env`
- **THEN** the worker process sees all of them

### Requirement: Required variables are guarded
The Compose file SHALL stop with a message naming the variable when a required one (database and broker credentials, JWT secret) is missing.

#### Scenario: Missing JWT secret
- **WHEN** `docker compose config` runs without `JWT_SECRET_KEY`
- **THEN** it fails with a message naming JWT_SECRET_KEY

### Requirement: Data survives recreation
The Compose file SHALL keep PostgreSQL and RabbitMQ data in named volumes, so that `docker compose down` followed by `up` keeps datasets and results.

#### Scenario: Down and up
- **WHEN** the stack is stopped with `down` (without volume removal) and started again
- **THEN** previously stored data is still present

### Requirement: Worker health is observable
The Compose file SHALL define a healthcheck for the worker that passes only while its metrics endpoint answers.

#### Scenario: Worker running
- **WHEN** the worker has started
- **THEN** its container becomes healthy

### Requirement: Build inputs are pinned
The image build SHALL install a pinned Poetry version equal to the one used in CI and SHALL use a base image tag that names its patch version, and the project dependencies SHALL NOT use unbounded `*` constraints.

#### Scenario: Pinned versions
- **WHEN** the Dockerfile and `pyproject.toml` are inspected
- **THEN** Poetry and the base image have explicit versions and no runtime dependency is declared as `*`
