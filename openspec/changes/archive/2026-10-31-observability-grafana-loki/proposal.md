# Proposal

## Why

Kan card LLM-10 ("Добавить Grafana и Loki для сбора сервисных логов и метрик"). Today the platform has no structured logs and no metrics beyond what each component logs ad hoc with `logging.getLogger(__name__)`; nothing is scraped, stored or visualized. Operators cannot see API error rates, request or task latency, failed-task counts, model response time or per-request cost without reading raw container output. The card asks for Grafana and Loki, GDPR-aware (no sensitive data in logs or metrics), and six concrete signals: 500 count, request latency, task latency, failed-task count, model response time (TTFT) and generation speed, and cost per request/token.

## What Changes

- Add structured JSON logging across the API and worker processes (timestamp, level, logger name, and request/task correlation fields), replacing the current unconfigured root logger. Logs never carry prompts, model output, API keys or other secrets — this already applies to LLM provider calls (`AnthropicProvider._log`) and extends to every new log line.
- Add a Prometheus metrics endpoint exposing:
  - HTTP request count by status (so 500s are countable) and request latency, via ASGI middleware.
  - Taskiq task latency and failure count, via a `TaskiqMiddleware`.
  - Model call latency and token usage (already computed per result) as metrics, and an estimated cost metric reusing the existing pricing calculation (`components/core/pricing.py`; moved there from `components/metrics/pricing.py` during implementation — see design.md).
  - **BREAKING for none**: this is additive; no existing endpoint or response shape changes.
- Add Loki, Promtail, Prometheus and Grafana as Compose services (dev/local stack), with Grafana pre-wired to both data sources, bound to localhost like the other infra ports.
- Document how to run the stack and where each of the six required signals appears, in `docs/observability.md`.

**Out of scope** (recorded as tech debt, not built here):
- True Time To First Token: the LLM provider adapters call the SDKs non-streaming (one `await ... .create(...)` per request), so no first-token timestamp exists to measure. This proposal reports total provider-call latency and an approximate generation speed (`output_tokens / latency_ms`) instead, labeled as an approximation, and reports TTFT itself as unavailable rather than inventing a value. Real TTFT needs a streaming mode in both adapters — a separate, larger change. A tech-debt Kan card will track it.
- Centralized long-term log/metric storage, alerting rules, and dashboards beyond a minimal starter dashboard are not built now.

## Capabilities

### New Capabilities
- `observability`: structured logging without sensitive content, the metrics the platform exposes for API requests, background tasks and model calls, and the local Grafana/Loki/Prometheus stack that collects them.

### Modified Capabilities

## Impact

- Code: new `components/core/logging.py` (or similar) for log configuration; new ASGI metrics middleware in `api/middleware.py` or a sibling module; a new Taskiq middleware registered in `taskiq_app.py`; a new `/metrics` endpoint; reuse of the pricing cost calculation, which moved from `components/metrics/pricing.py` to `components/core/pricing.py` for the cost metric (see design.md).
- Config: new settings for log format/level (if not already env-driven) and a metrics-enabled flag.
- Dependencies: adds `prometheus-client` (the standard Prometheus instrumentation library; Grafana needs a metrics data source, and Prometheus is the natural pairing named implicitly by "metrics" in the card even though not named explicitly — recorded here as an assumption).
- Infra: `docker-compose.yml` gains `loki`, `promtail`, `prometheus` and `grafana` services, dev-only, ports bound to localhost.
- Docs: new `docs/observability.md`.
- No database migration, no API breaking change, no change to experiment-level metrics (`components/metrics` CSV/export stays as is).
