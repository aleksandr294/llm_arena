# Spec Delta

## ADDED Requirements

### Requirement: Truncated responses are marked
The system SHALL record `response_truncated` in the details of every evaluation outcome computed for a response that the platform cut at the storage limit, and SHALL NOT change the verdict because of it.

#### Scenario: Truncated JSON
- **WHEN** a json_valid evaluator runs on a response that was cut at the size limit
- **THEN** the outcome details contain response_truncated true next to the evaluator's own reason

#### Scenario: Complete response
- **WHEN** a response was not truncated
- **THEN** the outcome details do not claim truncation
