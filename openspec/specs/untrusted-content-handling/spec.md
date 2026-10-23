# untrusted-content-handling Specification

## Purpose

Defines how dataset content, prompt templates and model output are handled as untrusted data everywhere in the platform, so that prompt injection and hostile text cannot execute, escalate privileges, leak other users' data or secrets, or corrupt exports, logs and interfaces.

## Requirements

### Requirement: Prompts are built literally
The system SHALL build each prompt only by literal substitution of the two defined placeholders, SHALL NOT evaluate or interpret any other part of the template or of the case content, and SHALL NOT re-scan substituted content for placeholders.

#### Scenario: Template syntax in a case
- **WHEN** a case input contains `{input}`, `{expected_output}`, `{0}`, `{{x}}`, `${x}`, `%s` or `{__class__}`
- **THEN** the prompt contains that text unchanged

#### Scenario: Placeholder smuggling
- **WHEN** a case input contains the text of the other placeholder
- **THEN** it is not substituted

### Requirement: Model output is data
The system SHALL store and return model output as plain text, SHALL NOT execute, evaluate or render it, and SHALL NOT use it to select tools, queries, files, URLs, users, organizations, evaluators or any configuration.

#### Scenario: Output that looks like code or commands
- **WHEN** a model response contains shell commands, SQL, Python, JSON with a `tool_call` field, HTML with script tags or markdown links
- **THEN** it is stored and returned unchanged as a string and nothing is executed or fetched

### Requirement: Injection cannot cross tenants or roles
The system SHALL ensure that text in a dataset, template or model response cannot cause another user's or organization's data to be read, written or included in a prompt, cannot change the owner, organization, role, evaluator configuration or pricing of any resource, and cannot reveal secrets or other users' system prompts.

#### Scenario: Instruction to reveal data
- **WHEN** a case input or a model response instructs the system to show another user's datasets, keys or settings
- **THEN** no such data is read and the stored result contains only the model's text

### Requirement: Safe sinks
The system SHALL emit untrusted text through encoders appropriate to each sink: JSON strings in API responses and JSON export, spreadsheet-safe cells in CSV export, escaped or plain data in the admin panel's API responses, and never into log messages.

#### Scenario: Hostile text in exports
- **WHEN** a response starts with `=`, `+`, `-`, `@`, a tab or a carriage return, or contains quotes, newlines, NUL replacements or very long lines
- **THEN** CSV cells are neutralized, JSON round-trips exactly and parsing the export does not break

#### Scenario: Hostile text in logs
- **WHEN** hostile text is processed in any request or task
- **THEN** no log record contains it

### Requirement: Evaluators stay deterministic and bounded
The system SHALL evaluate responses only with deterministic evaluators that never compile patterns from, or execute, response text, and SHALL finish within bounded time and memory for hostile input such as deeply nested JSON or very long strings.

#### Scenario: Hostile response to evaluators
- **WHEN** a response is a deeply nested JSON document or a multi-megabyte string
- **THEN** every evaluator returns an outcome without crashing the task

### Requirement: Stored output size is bounded
The system SHALL bound the size of stored and returned model output per result, truncating it with an explicit marker and recording that truncation happened, so that a hostile or misbehaving model cannot exhaust storage or memory.

#### Scenario: Oversized response
- **WHEN** a provider returns text larger than the configured limit
- **THEN** the stored response is truncated at the limit, carries the truncation marker, and the result records that it was truncated

### Requirement: Rendered prompt size is bounded
The system SHALL bound the size of each rendered prompt and of all prompts of one experiment, checking the sizes before any prompt is built or stored, so that a template repeating a placeholder cannot exhaust memory or storage.

#### Scenario: Amplifying template
- **WHEN** an experiment is created with a template that repeats `{input}` so that a rendered prompt would exceed the limit
- **THEN** the request is rejected with a validation error and nothing is stored
