# Design

## Context

`AnthropicProvider.generate` builds `params` and calls `self._client.messages.create(..., **params)`. In SDK 1.15.0 the signature of `AsyncMessages.create` has `extra_body` but no `temperature` (checked with `inspect.signature`). The adapter unit tests replace the client with a mock, so they accept any keyword. See proposal.md for the symptom.

## Goals / Non-Goals

**Goals:**
- A requested temperature is accepted by the SDK and sent to the API.
- A future unsupported keyword fails a unit test instead of a production run.

**Non-Goals:**
- Changing the accepted temperature range (0 to 2, shared by all providers). Anthropic documents 0 to 1 and answers a larger value with an `invalid_request` error, which is already normalized and not retried; narrowing the range per provider is a separate decision.
- Upgrading or pinning the SDK.
- Touching the OpenAI adapter.

## Decisions

- **`extra_body={"temperature": value}`** (chosen by the user). The SDK merges `extra_body` into the JSON body, so the API receives `temperature` and the SDK needs no named parameter. Alternatives: drop the temperature for Anthropic (silently ignores a caller's setting, contradicts reproducibility); upgrade the SDK to a version with a named parameter (none known to exist, and a dependency change is not justified); `extra_body` stays correct if a later SDK adds the named parameter.
- **Signature-based regression test.** Compare the keys of `create.call_args.kwargs` with `inspect.signature(AsyncMessages.create).parameters`, with every optional request field set. This guards the whole call, not only `temperature`, and needs no network or API key. A mock-only assertion on `extra_body` would repeat the original blind spot.
- Add a short code comment saying why `extra_body` is used, since the reason is not visible from the code.

## Risks / Trade-offs

- [`extra_body` bypasses the SDK's typing] → the signature test and the exact-value assertion cover the shape; a real-provider check stays out of the default test run per CLAUDE.md.
- [A model that rejects `temperature` (some newer models fix it)] → the API error is normalized as `invalid_request`; no change in behavior from today's failure, which is worse.
