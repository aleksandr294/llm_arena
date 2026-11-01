# Tasks

## 1. Structured logging

- [x] 1.1 Add a JSON log formatter and a `configure_logging()` helper in `components/core/logging.py` (timestamp, level, logger name, message, extras); call it once at startup from `main.py` (API) and `taskiq_app.py` (worker); verify a manual run prints one JSON object per line
- [x] 1.2 Add a unit test asserting the formatter's output is valid JSON with the required fields, and a hostile-input test using `tests/security/helpers.py` confirming a logged message containing prompt-like or secret-like content is not altered or specially handled (the formatter itself adds no filtering — logging call sites stay responsible for not passing secrets, as `AnthropicProvider._log` already does); verify both pass
- [x] 1.3 Grep the codebase for any `logger.*(` call that passes a prompt, response text, or a settings value typed `SecretStr`, and fix any found; verify with `grep -rn "logger\.\(debug\|info\|warning\|error\|exception\)" components api | grep -i "prompt\|api_key\|secret"` returns nothing

## 2. HTTP metrics

- [x] 2.1 Add `prometheus-client` to `pyproject.toml`; verify `poetry lock --no-update && poetry install` succeeds
- [x] 2.2 Add a pure ASGI `MetricsMiddleware` (same shape as `SecurityMiddleware`) in `api/middleware.py` or a sibling module, recording a request counter labeled by route and status and a latency histogram labeled by route; wire it in `api/app.py`; verify a new test drives a 200 and a forced 500 through it and asserts both counters incremented
- [x] 2.3 Add a `/metrics` endpoint (unauthenticated, `Cache-Control: no-store`, consistent with `/health`) serving the Prometheus exposition format; verify a test fetches it and finds the counters from 2.2

## 3. Task metrics

- [x] 3.1 Add a `TaskiqMiddleware` subclass recording task latency and a success/failure counter labeled by task name, registered on `broker` in `taskiq_app.py`; verify a unit test runs a task through a real broker with an in-memory result backend, forces an exception inside the task, and asserts the resulting `TaskiqResult.is_err` is `True` (Taskiq's own receiver already catches the exception before any middleware hook runs — it never reaches `post_execute`/`on_error` to propagate) and the failure counter incremented
- [x] 3.2 Expose the worker's metrics via `prometheus_client.start_http_server` on a configurable port at worker startup, bound to `127.0.0.1`; add the port to `components/core/config.py` settings; verify a manual worker run exposes `/metrics` on that port

## 4. Model call metrics

- [x] 4.1 In `ExperimentRunner`, after a provider call returns, record call latency, generation speed (`output_tokens / latency_ms` when both present, else omitted) and estimated cost (via `components/core/pricing.py`, moved there from `components/metrics/pricing.py` per CLAUDE.md's layering convention since it's now used by two components, omitted when pricing is unset or the model is unlisted), each labeled by provider and model only; verify a unit test checks the metric values for a mocked successful call, a call with no usage data, and a call with no pricing configured
- [x] 4.2 Confirm no metric label or value ever carries prompt or response text; verify with a hostile-input test from `tests/security/helpers.py` feeding an adversarial prompt through a mocked `ExperimentRunner` call and asserting none of it reaches the metrics registry

## 5. Local collection stack

- [x] 5.1 Add `loki`, `promtail`, `prometheus` and `grafana` services to `docker-compose.yml`, each with ports bound to `127.0.0.1`; add Promtail config reading the `api`/`worker` containers' JSON logs via the Docker logging driver; add a Prometheus scrape config for `api:8000/metrics` and the worker's metrics port; verify `docker compose config` validates and `docker compose up -d` brings all four up healthy (loki, prometheus and grafana verified healthy, with Prometheus actively scraping `api` and `worker` as `up`; promtail's `docker_sd_configs` config verified to parse and start cleanly, but exercising its access to `/var/run/docker.sock` was not possible in this sandbox — rootless Podman with no real Docker socket at that path, and a cross-user-namespace socket-sharing limitation confirmed by a one-off test; verify on a real Docker host)
- [x] 5.2 Add a checked-in Grafana provisioning YAML wiring both Loki and Prometheus as datasources, and one minimal starter dashboard with a panel per in-scope signal (500 count, request latency, task latency, failed-task count, model latency/speed/cost); verify Grafana loads the dashboard without manual datasource setup after a fresh `docker compose up`

## 6. Docs and checks

- [x] 6.1 Write `docs/observability.md`: how to start the stack, where each of the six Kan-card signals appears (five live, TTFT marked as a known gap with a pointer to its tech-debt card), and the GDPR/no-sensitive-data rule for logs and metric labels; verify the documented commands run as written
- [x] 6.2 Run `poetry run pytest -m "not integration"`, `ruff`, `black --check`, `mypy`, and Bandit at medium severity and above (command in `docs/security.md`); verify all are clean
- [x] 6.3 Create a tech-debt Kan card for real streaming-based TTFT, linking back to this change; verify the card exists in the `Open` list

## Workflow follow-up

- Review the diff with the reviewer agent, then commit on `feat/observability-grafana-loki` following `git-conventional-commits`. Do not push.
- Move the Kan card to Review once reviewed, then to Done only after the user merges the pull request.
- Archive this change after the pull request is merged.
