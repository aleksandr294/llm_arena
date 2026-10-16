# Spec Delta

## Purpose

Defines how the platform sends a request to an LLM provider and receives a provider-independent result or error, so experiments can compare models without provider-specific logic.

## ADDED Requirements

### Requirement: Provider and model chosen per call
The system SHALL select the provider and model from the configuration supplied with each request, and SHALL NOT depend on a process-wide default provider or model.

#### Scenario: Two providers in one process
- **WHEN** one request targets an OpenAI model and another targets an Anthropic model
- **THEN** each is sent to its own provider and neither affects the other

#### Scenario: Unsupported provider
- **WHEN** a request names a provider the system does not support
- **THEN** it fails with an error that names the provider and sends nothing

### Requirement: Normalized response
The system SHALL return, for a successful request, the generated text, the provider name, the requested model, the model name reported by the provider, the finish reason and the provider request id, in the same shape for every provider.

#### Scenario: Same shape across providers
- **WHEN** the same prompt is sent to OpenAI and to Anthropic
- **THEN** both responses expose the same fields

#### Scenario: Request id unavailable
- **WHEN** the provider returns no request id
- **THEN** the request id is null

### Requirement: Token usage reported as measured or unknown
The system SHALL report input, output and total token counts exactly as the provider returns them, and SHALL set any count the provider does not return to null.

#### Scenario: Usage returned
- **WHEN** the provider reports 12 input and 30 output tokens
- **THEN** the response contains 12 input and 30 output tokens

#### Scenario: Usage missing
- **WHEN** the provider response has no usage data
- **THEN** all token counts are null, not zero

### Requirement: Measured latency
The system SHALL report the elapsed time of the provider call in milliseconds, measured by the system, for every successful response.

#### Scenario: Latency present
- **WHEN** a request completes
- **THEN** the response contains a non-negative latency in milliseconds

### Requirement: Request parameters
The system SHALL accept a prompt, an optional system prompt, an optional temperature, an optional maximum output length and a timeout, and SHALL reject an empty prompt, a non-positive timeout or maximum output length, or a temperature outside 0 to 2 before contacting the provider.

#### Scenario: Empty prompt
- **WHEN** a request has an empty prompt
- **THEN** it is rejected and no provider call is made

#### Scenario: Optional parameters omitted
- **WHEN** temperature and maximum output length are omitted
- **THEN** the provider's defaults apply and no value is invented, except where the provider requires a value

#### Scenario: Provider requires a maximum output length
- **WHEN** a request to a provider that requires a maximum output length omits it
- **THEN** the system applies a documented fallback and the response reports the effective maximum output length used

### Requirement: Normalized errors
The system SHALL convert provider failures into one error type carrying a kind (timeout, rate_limit, authentication, invalid_request, server_error, malformed_response or unknown), the provider name, a retryable flag and, when available, the provider request id and retry-after delay.

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

### Requirement: Secure API key handling
The system SHALL read provider API keys only from configuration, SHALL fail with a clear error when the key for the chosen provider is not configured, and SHALL NOT write API keys or prompt and response content to logs or error messages.

#### Scenario: Key not configured
- **WHEN** a request targets a provider whose API key is not set
- **THEN** it fails with an error naming the missing setting and sends nothing

#### Scenario: Key not exposed
- **WHEN** settings are printed or a provider error is raised
- **THEN** the API key does not appear in the output
