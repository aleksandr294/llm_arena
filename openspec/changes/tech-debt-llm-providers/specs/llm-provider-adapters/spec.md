# Spec Delta

## MODIFIED Requirements

### Requirement: Normalized errors
The system SHALL convert provider failures into one error type carrying a kind (timeout, rate_limit, authentication, invalid_request, server_error, malformed_response, connection or unknown), the provider name, a retryable flag and, when available, the provider request id and retry-after delay. A retry-after value that is not a finite non-negative number SHALL be treated as absent.

#### Scenario: Rate limited
- **WHEN** the provider answers with a rate-limit error
- **THEN** the error kind is rate_limit and it is marked retryable

#### Scenario: Authentication failure
- **WHEN** the provider rejects the API key
- **THEN** the error kind is authentication and it is marked not retryable

#### Scenario: Timeout
- **WHEN** the provider does not answer within the request timeout
- **THEN** the error kind is timeout and it is marked retryable

#### Scenario: Malformed response
- **WHEN** the provider returns a response with no usable text content
- **THEN** the error kind is malformed_response

#### Scenario: Connection failure
- **WHEN** the connection to the provider cannot be established or is dropped
- **THEN** the error kind is connection and it is marked retryable

#### Scenario: Infinite retry-after
- **WHEN** the provider sends a retry-after header of inf or nan
- **THEN** the error carries no retry-after delay

## ADDED Requirements

### Requirement: Usage kept when no text is returned
The system SHALL keep the finish reason and the provider-reported token usage on the malformed_response error raised for an empty completion, and the experiment result SHALL store them when present.

#### Scenario: Reasoning model exhausts max tokens
- **WHEN** a model returns empty content with finish reason length and reports usage
- **THEN** the failed result stores the finish reason and the reported token counts

#### Scenario: Usage not reported
- **WHEN** the provider reports no usage for an empty completion
- **THEN** the stored token counts are unknown, not zero

### Requirement: Cached input tokens are counted
The system SHALL report Anthropic input tokens as the sum of regular, cache creation and cache read input tokens when the provider reports them, and as the regular count alone otherwise.

#### Scenario: Cache read present
- **WHEN** Anthropic reports 10 input tokens and 90 cache read tokens
- **THEN** the normalized input token count is 100

### Requirement: Optional time to first token
The system SHALL, when streaming measurement is enabled, return the elapsed milliseconds from sending the request to the first non-empty text chunk on the normalized response, and SHALL leave it unknown when streaming is disabled or no text chunk arrived. The text, finish reason, usage and request id SHALL be identical to the non-streaming result.

#### Scenario: Streaming enabled
- **WHEN** a request completes with streaming measurement on
- **THEN** the response has a non-negative time to first token that is not greater than the latency

#### Scenario: Streaming disabled
- **WHEN** a request completes with streaming measurement off
- **THEN** the time to first token is unknown

#### Scenario: Stream fails midway
- **WHEN** the stream is dropped after the first chunk
- **THEN** the error is normalized like any other provider failure and no partial text is returned
