# Spec Delta

## ADDED Requirements

### Requirement: Temperature reaches the provider
The system SHALL deliver a requested temperature to the provider with the value the caller gave, through every adapter, without the call being rejected by the provider's client library.

#### Scenario: Temperature requested from Anthropic
- **WHEN** a request with a temperature is sent to an Anthropic model
- **THEN** the call is made without a client-side error and the provider receives that temperature in the request

#### Scenario: Temperature omitted
- **WHEN** a request without a temperature is sent to an Anthropic model
- **THEN** no temperature is sent and the provider's default applies

#### Scenario: Call arguments match the client library
- **WHEN** an adapter builds a provider call for a request with every optional parameter set
- **THEN** every named argument it passes is accepted by the installed client library's method signature
