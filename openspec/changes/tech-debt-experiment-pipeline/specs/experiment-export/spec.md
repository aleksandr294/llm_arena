# Spec Delta

## ADDED Requirements

### Requirement: Failed export is not mistaken for complete
The system SHALL abort the response when streaming fails after it started, so that clients see a broken transfer instead of a CSV or JSON document that looks complete.

#### Scenario: Database failure mid-stream
- **WHEN** a batch read fails after the first rows were sent
- **THEN** the response ends with an error and the failure is logged without row content

### Requirement: Export snapshot semantics
The system SHALL document that the header and comparison reflect the experiment when streaming started, and that rows of a running experiment may be newer, and SHALL include the finished flag in the JSON header.

#### Scenario: Running experiment
- **WHEN** a running experiment is exported
- **THEN** the header has finished false

### Requirement: Neutralization covers whitespace and pipe
The system SHALL neutralize CSV text cells that start with a formula character after leading whitespace, and cells that start with a pipe.

#### Scenario: Leading space before equals
- **WHEN** a response text is " =1+1"
- **THEN** the CSV cell is prefixed with a single quote
