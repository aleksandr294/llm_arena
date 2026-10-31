"""The one Prometheus registry shared by everything that runs inside the
worker process (task metrics, model-call metrics), served by a single
``/metrics`` HTTP server started once at worker startup."""

from prometheus_client import CollectorRegistry

WORKER_METRICS_REGISTRY: CollectorRegistry = CollectorRegistry()
