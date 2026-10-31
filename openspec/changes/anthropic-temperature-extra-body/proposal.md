# Proposal

## Why

Every experiment run against an Anthropic model that sets a temperature fails: the worker log shows `AsyncMessages.create() got an unexpected keyword argument 'temperature'`. The installed Anthropic SDK (1.15.0) has no `temperature` parameter on `messages.create`, but the adapter passes it as a keyword. The mocked adapter test could not catch this because a mock accepts any keyword.

## What Changes

- Send the temperature to the Anthropic API in the request body through the SDK's `extra_body` argument instead of a named keyword, so the call is accepted by the SDK and the value still reaches the provider.
- Add a regression test that checks the keywords of the adapter's call against the real signature of `AsyncMessages.create`, so an unsupported keyword fails in unit tests.
- Update the existing optional-parameters test to the new call shape.

## Capabilities

### New Capabilities

### Modified Capabilities
- `llm-provider-adapters`: add a requirement that a requested temperature reaches the provider and is not rejected by the client library.

## Impact

- Code: `components/third_party/llm/providers/anthropic.py`.
- Tests: `tests/llm/test_anthropic_provider.py`.
- No API, schema, migration, configuration or dependency change. The OpenAI adapter is unaffected (its `create` accepts `temperature`).
