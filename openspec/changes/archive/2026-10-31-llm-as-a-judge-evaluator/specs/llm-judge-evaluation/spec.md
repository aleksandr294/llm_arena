# Spec Delta

## Purpose

Defines the `llm_judge` evaluator: a non-deterministic evaluator that asks a configured LLM to judge a generated response against free-text criteria, and how its verdict is produced, bounded and stored without ever becoming part of a slow database transaction.

## ADDED Requirements

### Requirement: Judge evaluator configuration
The system SHALL accept an `llm_judge` entry in the experiment evaluator configuration with a unique `id`, a non-empty `criteria` string, and a `provider` and `model` naming the judge's own target, independent of the experiment's target models. Unknown provider, missing criteria, or a criteria string over the configured length limit SHALL be rejected with 422 naming the field.

#### Scenario: Valid judge configuration
- **WHEN** an experiment is created with an `llm_judge` evaluator naming a criteria string, a provider and a model
- **THEN** the configuration is accepted and stored with the experiment, alongside any deterministic evaluators

#### Scenario: Missing criteria rejected
- **WHEN** an `llm_judge` entry omits `criteria`
- **THEN** experiment creation is rejected with 422 naming the field

### Requirement: Verdict is pass or fail
The system SHALL report an `llm_judge` evaluator's verdict using the same outcome status as every other evaluator (`passed`, `failed`, `skipped` or `error`), and SHALL NOT invent a numeric score or any outcome value outside that set.

#### Scenario: Judge passes a response
- **WHEN** the configured judge model's response to the grading prompt indicates the response meets the criteria
- **THEN** the stored outcome status is `passed`

#### Scenario: Judge fails a response
- **WHEN** the configured judge model's response indicates the response does not meet the criteria
- **THEN** the stored outcome status is `failed`

### Requirement: No open transaction during a judge call
The system SHALL make the judge's provider call with no database session or transaction open, and SHALL persist the resulting outcome in a separate, subsequent transaction.

#### Scenario: Deterministic evaluators are unaffected
- **WHEN** an experiment result has both deterministic and `llm_judge` evaluators configured
- **THEN** the deterministic evaluators' outcomes are computed and stored exactly as they are without any `llm_judge` evaluator configured, and the judge call happens separately

### Requirement: Judge failures never leave a result pending
The system SHALL store `error` for an `llm_judge` evaluator when the judge call times out, is rate-limited, fails for any other provider reason, or returns a response the system cannot parse into a verdict, and the stored detail SHALL carry only an error or exception kind, never provider error text or response content.

#### Scenario: Judge call fails
- **WHEN** the judge provider call raises a normalized provider error
- **THEN** the stored outcome status is `error` and its detail names only the error kind

#### Scenario: Judge response is unparseable
- **WHEN** the judge model's response cannot be parsed into a pass or fail verdict
- **THEN** the stored outcome status is `error`, not treated as a pass or a fail

### Requirement: Untrusted content is never stored back
The system SHALL treat the response being judged, the test case's input and expected output, and the judge model's own free-text output as untrusted, and SHALL NOT store any of that text in the evaluation outcome's detail — only reason codes and configured identifiers, consistent with every other evaluator.

#### Scenario: Hostile response text does not reach stored details
- **WHEN** the response being judged contains adversarial text aimed at the judge itself
- **THEN** the stored outcome's detail contains no part of that text, regardless of the verdict reached

#### Scenario: Judge's own explanation is not persisted
- **WHEN** the judge model returns a verdict together with a free-text explanation
- **THEN** the explanation text is not written to the stored outcome
