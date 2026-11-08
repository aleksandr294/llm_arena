# Proposal

## Why

Code review slices 2 and 3 left open findings in the generation, evaluation
and export pipeline (Kan cards `172m85cmpajg`, `q4saebmpzvc8`,
`rqepqhnk0tdu`): a worker can burn its 1800 s lease while waiting for a
provider slot and cause duplicate provider requests, a cancelled worker leaves
a result `running`, a platform-truncated response is scored as a model
failure and invisible in `/cases`, and large exports are roughly quadratic
because no index backs the export ordering. All three sit on the same
result rows and files, so they are one change.

## What Changes

- The runner acquires the provider slot before it claims a result, so the
  lease only covers time spent on the model call.
- Worker cancellation marks the claimed result failed (`interrupted`) instead
  of leaving it running; a bad provider name or config after the claim fails
  the result with `configuration` instead of leaving it pending.
- The retry budget counts attempts across reclaims; `Retry-After` is honored
  up to a larger cap and jitter is applied after capping.
- Provider text is scrubbed of lone surrogates before storage and the fallback
  write is protected; skipped duplicate deliveries are logged with the reason.
- Evaluation carries `response_truncated`: stored evaluation `details`
  record it, and `/cases` results expose it.
- Export: a new index for the export ordering, a mid-stream failure aborts the
  response instead of ending it silently, CSV neutralization covers leading
  whitespace and a leading pipe, snapshot semantics are documented.
- Cleanups: stale `models.py` comment, misleading `original_chars` log,
  `backoff_delay` moves to core (evaluation stops importing `experiments.utils`), one `rollback` helper in
  `components/core/database.py` replaces direct `session.rollback()` calls in
  the runner and evaluation service, positional row indexing and untyped
  `defaultdict` in the export code.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `experiment-execution`: claim/lease ordering, interruption, configuration
  failures, retry budget, Retry-After.
- `deterministic-evaluation`: truncation flag in evaluation details.
- `experiment-metrics`: per-case results expose truncation.
- `experiment-export`: failure signalling, neutralization edge cases.

## Impact

- Code: `components/experiments/{runner,repository,utils,models}.py`, `components/core/utils.py`,
  `components/evaluation/{schemas,repository,service,tasks}.py`,
  `components/metrics/{repository,schemas,service,export}.py`,
  `components/core/database.py`, `components/metrics/utils.py` (neutralize).
- Database: migration `0009` (index only; no data change).
- API: `response_truncated` added to case results (additive).
- Docs: `docs/experiments-api.md`, `docs/evaluation-api.md`, `docs/metrics-api.md`.
- No new dependencies.
