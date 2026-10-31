# Observability

[Back to the README](../README.md)

## Overview

The platform emits structured JSON logs and Prometheus metrics from both
the API and the worker process. A local Compose stack collects them: Loki
for logs, Prometheus for metrics, Grafana to view both. See
[`openspec/specs/observability/spec.md`](../openspec/specs/observability/spec.md)
for the behavioral contract this implements.

## Running the stack

```bash
docker compose up -d
```

This starts `api`, `worker`, `postgres`, `rabbitmq` and the observability
services: `loki`, `promtail`, `prometheus`, `grafana`. Grafana is at
<http://localhost:3001> (not 3000 — that port is already used by the local
Kan board, see `CLAUDE.md`), default login `admin` / `admin`. Prometheus is
at <http://localhost:9090>. Both datasources (Loki and Prometheus) are
provisioned automatically; nothing needs to be configured by hand.

A starter dashboard ("LLM Arena - Starter") is provisioned automatically
with one panel per signal below. It uses `[5m]` rate windows, so give the
stack a few minutes of traffic before expecting non-empty graphs.

## Where each of the six signals from the Kan card appears

| # | Signal | Where | Status |
| - | ------ | ----- | ------ |
| 1 | Count of 500 errors | `http_requests_total{status="500"}` (Prometheus, scraped from the API's `/metrics`) | Live |
| 2 | Request execution time | `http_request_duration_seconds` (Prometheus, same source) | Live |
| 3 | Task execution time | `task_duration_seconds` (Prometheus, scraped from the worker's `/metrics`) | Live |
| 4 | Count of failed tasks | `task_runs_total{outcome="failed"}` (Prometheus, same source) | Live |
| 5 | Model response time (TTFT) and generation speed | `model_call_latency_seconds` and `model_call_generation_speed_tokens_per_second` (Prometheus, worker `/metrics`) | **Partial** — see below |
| 6 | Cost per request/token | `model_call_cost_estimate_total` (Prometheus, worker `/metrics`); absent when no pricing file is configured | Live |

### Known gap: true TTFT is not measured

The LLM provider adapters (`components/third_party/llm/providers/`) call
the SDKs non-streaming — one `await ... .create(...)` per request — so
there is no first-token timestamp to measure. `model_call_latency_seconds`
is the *total* call latency, and
`model_call_generation_speed_tokens_per_second` is an *approximation*
(`output_tokens / total_latency`), not a real time-to-first-token value.
Both are labeled and documented as such; neither is presented as TTFT.
Real TTFT needs a streaming mode in both provider adapters — tracked as a
separate Kan tech-debt card (see the `observability-grafana-loki` OpenSpec
change for the scope decision).

## GDPR / no-sensitive-data rule

Logs and metric labels never carry a prompt, model-generated text, an API
key, a password, or any other secret:

- Log lines are formatted by `components/core/logging.JSONFormatter`,
  which adds no filtering of its own — call sites stay responsible for
  never logging such content, the same rule `AnthropicProvider._log`
  already follows (ids, status, latency, error kind only). See
  `tests/core/test_logging.py` and `tests/experiments/test_model_metrics.py`
  for the hostile-input coverage.
- Metric labels are a fixed, small set: provider, model, HTTP route
  (matched route template, never a raw path), HTTP status, task name, and
  currency. None is derived from request or response text, so cardinality
  stays bounded and no sensitive content can reach a label.

## Logs: querying in Grafana

Promtail ships only the `api` and `worker` containers' stdout (JSON,
Docker's own `json-file` logging driver) to Loki — `postgres`, `rabbitmq`
and the observability stack itself are not collected. In Grafana's Explore
view, select the Loki datasource and query, for example:

```
{service="api"} | json
```

## Configuration

| Setting | Default | Purpose |
| ------- | ------- | ------- |
| `LOG_LEVEL` | `INFO` | Root logger level; the handler is always JSON |
| `WORKER_METRICS_PORT` | `9100` | Where the worker serves `/metrics`, bound to `127.0.0.1` |

The API serves its own metrics at `/metrics` on its existing port
(unauthenticated, `Cache-Control: no-store`, like `/health`).

## Known limitations

- No alerting rules and no production-grade retention tuning — this is a
  local development stack, not a production observability deployment.
- The starter dashboard is minimal: one panel per signal, not a full
  operational dashboard.
- `components/metrics` (experiment-level CSV/summary metrics for comparing
  models) is unrelated to this document; it is not changed here.
- The worker runs with a single process (`--workers 1` in
  `docker-compose.yml`). Each Taskiq worker process would otherwise keep
  its own in-memory metrics registry and try to bind the same metrics
  port; scaling to several worker processes needs
  `prometheus_client`'s multiprocess mode instead, which is not
  implemented. Concurrency is still bounded within the one process by
  `LLM_MAX_CONCURRENCY` and Taskiq's own `--max-async-tasks`, not by the
  process count.
