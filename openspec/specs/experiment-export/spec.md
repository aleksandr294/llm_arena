# experiment-export Specification

## Purpose

Defines how an experiment's configuration, metrics and per-result data are exported as JSON or CSV so results can be analyzed and reproduced outside the platform without losing the distinction between measured and estimated values.

## Requirements

### Requirement: JSON export
The system SHALL export an experiment as a JSON document containing the experiment's frozen configuration (prompt template, generation parameters, retry settings, evaluator configuration, targets), its status and whether it has finished, the comparison with the pricing version, and one entry per result with its case position, target, status, attempts, measured latency and tokens, error kind, evaluator outcomes and, unless excluded, the rendered prompt and response text.

#### Scenario: Export document
- **WHEN** a client exports a completed experiment as JSON
- **THEN** the document contains the frozen configuration, the comparison and every result row

#### Scenario: Unknown experiment
- **WHEN** a client exports an experiment that does not exist
- **THEN** the response is 404

### Requirement: CSV export
The system SHALL export an experiment as CSV with a header row and one row per result, with columns for case position, provider, model, status, attempts, latency, input, output and total tokens, error kind and one column per configured evaluator holding its outcome, and with response text included only when requested.

#### Scenario: CSV rows
- **WHEN** a client exports an experiment with 3 cases and 2 targets as CSV
- **THEN** the file has a header and 6 rows

#### Scenario: Unknown values empty
- **WHEN** a result has no reported tokens
- **THEN** its token cells are empty, not zero

### Requirement: CSV formula injection protection
The system SHALL neutralize any exported text cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return by prefixing it with a single quote, so spreadsheet software does not execute model output or dataset content as a formula.

#### Scenario: Hostile response
- **WHEN** a response starts with `=HYPERLINK(...)` and responses are included
- **THEN** the exported cell starts with a single quote followed by the original text

### Requirement: Bounded export
The system SHALL stream exports in bounded batches rather than building the whole document in memory, SHALL export experiments in any status, and SHALL report in the export whether the experiment has finished so partial exports are not mistaken for final ones.

#### Scenario: Partial export
- **WHEN** a client exports an experiment that is still running
- **THEN** the export succeeds and states that it is not finished

### Requirement: Export inputs are untrusted
The system SHALL emit stored values as data only, SHALL encode them according to the format, and SHALL NOT log exported content.

#### Scenario: Special characters
- **WHEN** a response contains quotes, commas, newlines and non-ASCII text
- **THEN** the CSV and JSON exports parse back to the exact stored text (apart from the CSV formula prefix rule)
