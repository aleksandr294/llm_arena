# deterministic-evaluation Specification

## Purpose

Defines deterministic evaluators and how their outcomes for generated responses are configured, computed and stored separately from the responses, so results can be explained and recomputed without calling an LLM.

## Requirements

### Requirement: Evaluator configuration
The system SHALL accept an experiment evaluator configuration of the form `{"evaluators": [...]}`, where each entry has a unique `id` (1 to 64 characters), a known `type` and that type's parameters. An empty configuration or an empty list SHALL mean no evaluation. Unknown types, duplicate ids, unknown or invalid parameters and more than 20 entries SHALL be rejected with 422 naming the field.

#### Scenario: Valid configuration
- **WHEN** an experiment is created with an exact_match and a json_valid evaluator
- **THEN** the configuration is stored with the experiment

#### Scenario: Unknown type
- **WHEN** an evaluator entry has an unknown type
- **THEN** the response is 422 and the error identifies the evaluator entry

#### Scenario: Duplicate id
- **WHEN** two evaluator entries share an id
- **THEN** the response is 422

### Requirement: Evaluator contract and outcomes
The system SHALL run each evaluator as a pure function of the response text, the case's expected output and the evaluator's parameters, and SHALL return one outcome: passed, failed, skipped (the check does not apply because a required input is absent) or error (the evaluator could not run). Each outcome SHALL carry machine-readable details that contain no response text.

#### Scenario: Deterministic
- **WHEN** the same response, expected output and parameters are evaluated twice
- **THEN** the outcomes and details are identical

#### Scenario: No model call
- **WHEN** a response is evaluated
- **THEN** no request is made to any LLM provider

### Requirement: Exact match
The exact_match evaluator SHALL pass when the response equals the case's expected output, with optional trimming of surrounding whitespace (default on), case-insensitive comparison (default off) and whitespace collapsing (default off). It SHALL be skipped when the case has no expected output.

#### Scenario: Equal after trim
- **WHEN** the response is " Paris\n" and the expected output is "Paris"
- **THEN** the outcome is passed

#### Scenario: Different text
- **WHEN** the response is "Lyon" and the expected output is "Paris"
- **THEN** the outcome is failed

#### Scenario: No expected output
- **WHEN** the case has no expected output
- **THEN** the outcome is skipped

### Requirement: Contains and not contains
The contains evaluator SHALL pass when the response includes all (default) or any of 1 to 20 configured substrings, and the not_contains evaluator SHALL pass when it includes none of them; both SHALL support case-insensitive matching (default off).

#### Scenario: All present
- **WHEN** contains is configured with ["Paris", "France"] and the response has both
- **THEN** the outcome is passed

#### Scenario: Forbidden text present
- **WHEN** not_contains is configured with ["sorry"] and the response contains "sorry"
- **THEN** the outcome is failed

### Requirement: Length between
The length_between evaluator SHALL pass when the response length in characters is within the configured inclusive minimum and maximum, and at least one bound SHALL be required.

#### Scenario: Too long
- **WHEN** the maximum is 10 and the response has 11 characters
- **THEN** the outcome is failed

### Requirement: JSON validity
The json_valid evaluator SHALL pass when the whole response, after trimming whitespace, parses as JSON, optionally requiring the top-level value to be an object. A response that is not valid JSON, or nests too deeply to parse, SHALL fail rather than error.

#### Scenario: Valid object
- **WHEN** the response is `{"a": 1}` and an object is required
- **THEN** the outcome is passed

#### Scenario: Invalid JSON
- **WHEN** the response is `not json`
- **THEN** the outcome is failed

### Requirement: JSON required fields and types
The json_fields evaluator SHALL parse the response as a JSON object and pass when every configured required field path (dot-separated) is present, and every configured type constraint (string, number, integer, boolean, array, object or null) on a present field matches. The details SHALL list missing and mistyped paths.

#### Scenario: Missing field
- **WHEN** required fields are ["name", "address.city"] and the response lacks address.city
- **THEN** the outcome is failed and the details list address.city as missing

#### Scenario: Wrong type
- **WHEN** field age must be an integer and the response has "age": "7"
- **THEN** the outcome is failed and the details list age as mistyped

### Requirement: Evaluate successful generations separately
The system SHALL evaluate only results whose generation succeeded, in a task separate from generation, and SHALL store one evaluation result per generated result and evaluator with the evaluator id, type and version, the outcome and the details. Evaluation SHALL NOT change a result's or an experiment's generation status or modify the stored response.

#### Scenario: Stored separately
- **WHEN** a result succeeds and its experiment has 2 evaluators
- **THEN** 2 evaluation results are stored and the generated response is unchanged

#### Scenario: Failed generation
- **WHEN** a result failed during generation
- **THEN** no evaluation result is stored for it

### Requirement: Idempotent evaluation
The system SHALL store at most one evaluation result per generated result and evaluator, so redelivered tasks and repeated backfill requests do not duplicate or change an existing outcome.

#### Scenario: Duplicate delivery
- **WHEN** the evaluation task for a result is delivered twice
- **THEN** exactly one evaluation result per evaluator exists

### Requirement: Backfill evaluation
The system SHALL provide an endpoint that enqueues evaluation for succeeded results of an experiment that lack an evaluation result for some configured evaluator, return 202 with the number of results enqueued and how many still lack evaluation, and not report enqueueing as completion. The number enqueued per request SHALL be bounded, so a large backlog is processed in batches by requesting again.

#### Scenario: Missed trigger
- **WHEN** a result succeeded but its evaluation was never run, and a client requests evaluation
- **THEN** the response is 202 and that result is evaluated by the worker

#### Scenario: Nothing to do
- **WHEN** all succeeded results are already evaluated
- **THEN** the response is 202 with zero enqueued

### Requirement: Read evaluation results
The system SHALL list an experiment's evaluation results with pagination (limit 1 to 100, default 50, offset 0 or more), a total and optional filters by evaluator id and outcome, and SHALL return per evaluator the count of each outcome. An unknown experiment SHALL return 404.

#### Scenario: Summary counts
- **WHEN** evaluator exact_match has 3 passed and 1 failed outcomes
- **THEN** the summary for exact_match shows passed 3 and failed 1

#### Scenario: Unknown experiment
- **WHEN** a client reads evaluations of an experiment that does not exist
- **THEN** the response is 404

### Requirement: Evaluation inputs are untrusted
The system SHALL treat responses, expected outputs and configuration as untrusted data: it SHALL NOT execute or interpret them as code or patterns, and SHALL NOT write response text to logs or evaluation details.

#### Scenario: Hostile response
- **WHEN** a response contains very deep JSON nesting or a NUL byte
- **THEN** the evaluator returns an outcome without crashing the task and the stored details contain no response text
