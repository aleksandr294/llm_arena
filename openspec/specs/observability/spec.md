# observability Specification

## Purpose

Defines what the platform logs and measures about its own operation — API requests, background tasks and model calls — without exposing sensitive content, and how those signals reach Grafana through Loki and Prometheus.

## Requirements

### Requirement: Structured logs exclude sensitive content
The system SHALL emit logs in a structured, machine-parseable format carrying at least a timestamp, a level, the logger name and the message, and SHALL NOT include API keys, prompts, model-generated text, passwords or other secrets in any log line.

#### Scenario: Request log has no prompt or key content
- **WHEN** an API request or an LLM provider call is logged
- **THEN** the log line contains no prompt text, no model output text and no API key value

#### Scenario: Structured fields present
- **WHEN** any component logs an event
- **THEN** the log entry can be parsed into a timestamp, a level, a logger name and a message

### Requirement: HTTP error and latency metrics
The system SHALL expose, for every HTTP request handled by the API, a count broken down by response status and the request latency, through a metrics endpoint a scraper can poll.

#### Scenario: 500 is countable
- **WHEN** a request to any endpoint results in a 500 response
- **THEN** the exposed metrics include a counter that increments for that status

#### Scenario: Latency recorded
- **WHEN** a request completes with any status
- **THEN** the exposed metrics include that request's latency

### Requirement: Task latency and failure metrics
The system SHALL expose the execution time and outcome (succeeded or failed) of every background task run by the worker.

#### Scenario: Failed task counted
- **WHEN** a task raises an unhandled exception
- **THEN** the exposed metrics include a counter that increments for that failure

#### Scenario: Task latency recorded
- **WHEN** a task finishes, successfully or not
- **THEN** the exposed metrics include that task's execution time

### Requirement: Model call latency, generation speed and cost
The system SHALL expose, for every LLM provider call, the measured call latency, an approximate generation speed computed from output tokens and latency, and an estimated cost computed with the same pricing logic used for experiment cost estimates, and SHALL report a value as unavailable rather than inventing one when a measurement cannot be made.

#### Scenario: Generation speed is approximate, not true TTFT
- **WHEN** a model call completes
- **THEN** the exposed metrics include output tokens divided by call latency, and do not claim to be a time-to-first-token measurement

#### Scenario: Cost unknown when pricing is unset
- **WHEN** no pricing file is configured for a provider and model
- **THEN** the exposed cost metric for that call is omitted or marked unknown, never zero or invented

### Requirement: Local collection stack
The system's local Compose environment SHALL run Loki, Promtail, Prometheus and Grafana, with Grafana pre-configured with both Loki and Prometheus as data sources, and SHALL bind their ports to localhost like the project's other infrastructure services.

#### Scenario: Stack starts with the rest of the environment
- **WHEN** the Compose stack is started
- **THEN** Grafana is reachable on localhost and can query both the logs and the metrics collected from the API and worker
