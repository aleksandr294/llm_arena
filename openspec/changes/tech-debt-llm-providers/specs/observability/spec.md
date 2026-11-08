# Spec Delta

## ADDED Requirements

### Requirement: Time to first token metric
The system SHALL expose a time-to-first-token histogram per provider and model for calls made with streaming measurement enabled, separate from the generation speed approximation, and SHALL NOT record it when no first-token time was measured.

#### Scenario: Measured
- **WHEN** a call completes with a first-token time
- **THEN** the exposed metrics include that time in seconds under a name that says time to first token

#### Scenario: Not measured
- **WHEN** a call completes without a first-token time
- **THEN** no time-to-first-token observation is recorded and the generation speed approximation is unchanged
