# Proposal

## Why

Kan card LLM-6 (stage 5 of the MVP plan). Experiments store per-case responses, measured latency and tokens, and evaluator outcomes, but there is no way to see how models differ. The platform's purpose is comparing models on quality, latency, tokens, estimated cost and error rate from preserved data, and handing that data to other tools, without inventing values the providers did not report.

## What Changes

- Add an experiment summary and a side-by-side comparison of the experiment's targets (provider/model), computed from stored results only: result counts by status, error rate and error kinds, latency statistics, token totals with coverage, evaluator pass rates, and an estimated cost.
- Add a per-case view (one row per test case with each target's outcome).
- Add JSON and CSV export of the experiment: frozen configuration, comparison and per-result rows.
- Add versioned pricing: an optional pricing file supplied by the operator; estimated costs are computed from reported tokens and always carry the pricing version, currency and the rates used. Without a pricing file, or for an unpriced model, cost is null.
- Distinguish measured values (tokens, latency, counts) from estimated ones (cost) in every response, and record unknown values as null with a count of how many results lacked them.

## Capabilities

### New Capabilities
- `experiment-metrics`: summary, comparison, per-case view and cost estimation for an experiment.
- `experiment-export`: JSON and CSV export of an experiment's configuration, metrics and results.

### Modified Capabilities

## Impact

- New component `components/metrics/` (pricing, schemas, repository, service, export) and `api/v1/endpoints/metrics.py`, registered in `api/v1/router.py`.
- Optional `PRICING_FILE` setting in `components/core/config.py` and `.env.example`; an example pricing file under `docs/` or the component folder with clearly marked example values.
- Read-only: no database schema change, no migration, no new dependencies.
- Built on top of the `deterministic-evaluation` branch (evaluation outcomes feed the quality metrics).

## Assumptions

Recorded in design.md and revisable before apply: metric definitions (error rate, latency, percentiles, pass rate), cost computed at read time from an operator-supplied pricing file (no prices are shipped as facts), comparison scoped to the targets of one experiment, no ranking or composite score, response text only on request, and CSV formula-injection protection.

## Out of Scope

- Comparing different experiments, ranking models, composite quality scores, charts or any frontend.
- Persisting computed costs or snapshots; background report generation.
- Live pricing lookup from providers, currency conversion, batch or cached-token pricing tiers.
- LLM-as-a-judge and statistical significance tests.
