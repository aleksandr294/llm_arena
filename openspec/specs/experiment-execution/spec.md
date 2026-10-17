# experiment-execution Specification

## Purpose

Defines how an experiment runs one dataset against several models reliably and reproducibly, persisting every case result independently so partial failures, retries and duplicate task delivery never lose or duplicate results.

## Requirements

### Requirement: Create an experiment
The system SHALL create an experiment from an existing dataset, 1 to 10 distinct provider/model targets, a prompt template, generation parameters and an evaluator configuration. The response SHALL be 201 with status pending, and no model request SHALL be made.

#### Scenario: Experiment created
- **WHEN** a client creates an experiment for a dataset with 3 cases and 2 models
- **THEN** the response is 201, the status is pending and 6 pending results exist

#### Scenario: Unknown dataset
- **WHEN** a client creates an experiment for a dataset that does not exist
- **THEN** the response is 404 and nothing is stored

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

### Requirement: Frozen configuration and case set
The system SHALL store the prompt template, generation parameters, retry settings, evaluator configuration and the set of test cases at creation, and SHALL NOT change them when the dataset is later modified.

#### Scenario: Dataset grows after creation
- **WHEN** a case is added to the dataset after the experiment was created
- **THEN** the experiment still has results only for the original cases

### Requirement: Prompt rendering
The system SHALL build each prompt by replacing `{input}` and, if present, `{expected_output}` in the template with the case values, treating all other text, including other braces, as literal and never re-interpreting case content.

#### Scenario: Braces in case input
- **WHEN** a case input contains `{input}` or `{0}`
- **THEN** the prompt contains that text unchanged

### Requirement: Asynchronous start
The system SHALL start a pending experiment by enqueueing one task per pending result and returning 202 without waiting for completion. The experiment SHALL be running, and the response MUST NOT claim any result has completed.

#### Scenario: Start
- **WHEN** a client starts a pending experiment
- **THEN** the response is 202, the status is running and results are produced later by the worker

#### Scenario: Start again after a lost message
- **WHEN** a client starts a running experiment that still has pending results
- **THEN** tasks are enqueued again only for the pending results and for results running longer than the lease

#### Scenario: Start finished experiment
- **WHEN** a client starts a completed, failed or cancelled experiment
- **THEN** the response is 409

### Requirement: Independent result persistence
The system SHALL persist each result as soon as it finishes, with its status, response text, finish reason, provider request id, reported model, input/output/total tokens, latency in milliseconds, error kind and attempt count. Values the provider did not report SHALL be null.

#### Scenario: One case fails
- **WHEN** 1 of 3 cases fails permanently
- **THEN** the other 2 results remain succeeded with their stored responses

#### Scenario: Usage unknown
- **WHEN** the provider returns no token usage
- **THEN** the token fields of the result are null, not zero

### Requirement: Bounded concurrency
The system SHALL limit the number of simultaneous model requests per provider in a worker to a configured maximum (default 4).

#### Scenario: More tasks than the limit
- **WHEN** 20 results for one provider are executing in a worker with a limit of 4
- **THEN** no more than 4 model requests are in flight at once

### Requirement: Timeout and retry policy
The system SHALL apply the experiment's per-request timeout, which MUST be above 0 and at most 120 seconds (422 otherwise), and SHALL retry a result only when the provider error is marked retryable, up to the experiment's max attempts (default 3) with exponential backoff capped at 30 seconds, honoring a provider retry-after delay up to that cap. Non-retryable errors SHALL fail the result immediately.

#### Scenario: Transient rate limit
- **WHEN** a request fails with a rate_limit error and then succeeds on the second attempt
- **THEN** the result is succeeded with 2 attempts

#### Scenario: Authentication error
- **WHEN** a request fails with an authentication error
- **THEN** the result is failed after 1 attempt with error kind authentication

#### Scenario: Attempts exhausted
- **WHEN** a retryable error persists for the max attempts
- **THEN** the result is failed with the last error kind and the attempt count

### Requirement: Safe duplicate delivery
The system SHALL execute a result at most once concurrently and SHALL NOT repeat a finished result: a task delivered again for a result that is running, succeeded, failed or cancelled MUST be a no-op, and a result left running by a crashed worker for longer than the lease SHALL be claimable again.

#### Scenario: Duplicate delivery
- **WHEN** the same task is delivered twice and the first execution succeeded
- **THEN** exactly one model request was made and the stored result is unchanged

#### Scenario: Crashed worker
- **WHEN** a result has been running longer than the lease
- **THEN** a redelivered task executes it again

### Requirement: Experiment status
The system SHALL derive the experiment status from its results once none is pending or running: completed when all succeeded, partially_failed when some succeeded and some failed, failed when none succeeded, and cancelled when it was cancelled.

#### Scenario: Mixed outcome
- **WHEN** all results finished and 2 succeeded and 1 failed
- **THEN** the status is partially_failed

#### Scenario: Still running
- **WHEN** at least one result is pending or running
- **THEN** the status is running

### Requirement: Retry failed results
The system SHALL allow failed results of a partially_failed or failed experiment to be run again without changing succeeded results: the failed results become pending, the experiment becomes running, and tasks are enqueued only for them.

#### Scenario: Retry
- **WHEN** a client retries a partially_failed experiment with 1 failed and 2 succeeded results
- **THEN** only the failed result runs again and the 2 succeeded results are unchanged

#### Scenario: Nothing to retry
- **WHEN** a client retries a completed experiment
- **THEN** the response is 409

### Requirement: Cancel an experiment
The system SHALL cancel a pending or running experiment by marking its pending results cancelled, letting results already running finish and be stored, and then setting the experiment status to cancelled.

#### Scenario: Cancel while running
- **WHEN** a running experiment with 2 pending and 1 running result is cancelled
- **THEN** the 2 pending results are cancelled, the running result is stored when it finishes and the experiment ends as cancelled

#### Scenario: Cancel finished experiment
- **WHEN** a client cancels a completed experiment
- **THEN** the response is 409

### Requirement: Read experiments and results
The system SHALL return an experiment with its frozen configuration and a count of results per status, and SHALL list its results ordered by case position then target with pagination (limit 1 to 100, default 50, offset 0 or more), a total, and an optional status filter.

#### Scenario: Progress counts
- **WHEN** a client reads a running experiment with 1 succeeded and 2 pending results
- **THEN** the counts show 1 succeeded and 2 pending

#### Scenario: Unknown experiment
- **WHEN** a client reads an experiment id that does not exist
- **THEN** the response is 404

### Requirement: Errors and content are not leaked
The system SHALL store only the error kind and a sanitized message in a failed result, and SHALL NOT log or store API keys, and SHALL NOT write prompts or model responses to logs.

#### Scenario: Provider error text
- **WHEN** a provider error message contains prompt text
- **THEN** the stored error message and the logs do not contain it
