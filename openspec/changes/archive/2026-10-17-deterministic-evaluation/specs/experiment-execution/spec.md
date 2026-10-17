# Spec Delta

## MODIFIED Requirements

### Requirement: Experiment input validation
The system SHALL reject, with 422 naming the invalid field, an empty dataset, an empty or duplicate target list, more than 10 targets, a provider whose API key is not configured, a template without the `{input}` placeholder, generation parameters outside the provider request limits, max attempts outside 1 to 5, and an evaluator configuration that is not a valid deterministic-evaluation configuration.

#### Scenario: Template without input placeholder
- **WHEN** the prompt template does not contain `{input}`
- **THEN** the response is 422 and the error identifies the template field

#### Scenario: Provider not configured
- **WHEN** a target uses a provider whose API key is not set
- **THEN** the response is 422 and the error names the missing setting

#### Scenario: Invalid evaluator configuration
- **WHEN** the evaluator configuration is not a JSON object or contains an unknown evaluator type
- **THEN** the response is 422 and the error identifies the evaluator configuration field
