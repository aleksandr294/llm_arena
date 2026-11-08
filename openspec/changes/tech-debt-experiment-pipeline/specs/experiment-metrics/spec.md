# Spec Delta

## ADDED Requirements

### Requirement: Per-case results expose truncation
The system SHALL include `response_truncated` for each result in the per-case listing, whether or not responses are requested.

#### Scenario: Truncated result in /cases
- **WHEN** a client lists cases of an experiment that has a truncated response
- **THEN** that result shows response_truncated true and the others false
