# Spec Delta

## ADDED Requirements

### Requirement: Claim follows provider capacity
The system SHALL claim a result only after a provider request slot is available, so that waiting for capacity does not consume the result's lease.

#### Scenario: Waiting does not burn the lease
- **WHEN** more results are delivered than the provider concurrency allows
- **THEN** results waiting for a slot are still pending and are not reclaimed or executed twice

### Requirement: Interrupted execution is reported
The system SHALL mark a claimed result failed with error kind `interrupted` when its worker is cancelled before the outcome is stored, and SHALL allow it to be retried like any failed result.

#### Scenario: Worker cancelled
- **WHEN** the worker task is cancelled during a model call
- **THEN** the result becomes failed with kind interrupted and the experiment is finalized from the stored results

### Requirement: Invalid target or configuration fails the result
The system SHALL mark a result failed with error kind `configuration` when its provider name or stored configuration cannot be used after it was claimed, instead of leaving it pending.

#### Scenario: Unknown provider in a stored result
- **WHEN** a claimed result names a provider the platform does not know
- **THEN** the result is failed with kind configuration and no provider call is made

### Requirement: Retry budget spans reclaims
The system SHALL count provider requests across reclaims of the same result, so that a result never makes more requests than the configured maximum attempts in one run of the experiment.

#### Scenario: Reclaimed result
- **WHEN** a result with 2 of 3 attempts used is reclaimed after a crash
- **THEN** it makes at most one more request

### Requirement: Retry-After is honored with jitter
The system SHALL wait at least the provider-supplied retry delay up to 120 seconds, SHALL add jitter after the cap is applied, and SHALL fail the result with error kind `rate_limit` instead of waiting when the provider asks for more than the cap.

#### Scenario: Provider asks for 60 seconds
- **WHEN** a provider answers 429 with a retry delay of 60 seconds
- **THEN** the next request is delayed by at least 60 seconds

#### Scenario: Provider asks for 10 minutes
- **WHEN** a provider answers 429 with a retry delay of 600 seconds
- **THEN** the result fails with kind rate_limit without sleeping

### Requirement: Provider text is always storable
The system SHALL replace unpaired surrogate characters in provider text with U+FFFD before storing it, and SHALL keep the result from remaining running when the outcome write fails.

#### Scenario: Lone surrogate
- **WHEN** a provider returns text containing an unpaired surrogate
- **THEN** the result is stored as succeeded with the character replaced

#### Scenario: Both writes fail
- **WHEN** the outcome write and the fallback failure write both fail
- **THEN** the error is logged with the result id and the task fails visibly so it is redelivered

### Requirement: Skipped deliveries are explained
The system SHALL log, for every delivery it skips, the result id and the reason (finished or owned by another worker).

#### Scenario: Duplicate delivery
- **WHEN** a result that is already succeeded is delivered again
- **THEN** no provider call is made and the log names reason finished
