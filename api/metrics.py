"""Pure ASGI middleware exposing HTTP request metrics for Prometheus.

Written as plain ASGI, like ``SecurityMiddleware``, so it does not
interfere with streaming responses. Labels are bounded (route template and
status only) — never a raw path or anything else of unbounded cardinality.
"""

import time

from fastapi import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REGISTRY: CollectorRegistry = CollectorRegistry()

UNMATCHED_ROUTE = "unmatched"

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "HTTP requests by route and response status.",
    ("route", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by route.",
    ("route",),
    registry=REGISTRY,
)


async def metrics_endpoint() -> Response:
    """Prometheus exposition format. Unauthenticated, like ``/health``;
    never cached, since it reports live counters."""
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
        headers={"Cache-Control": "no-store"},
    )


class MetricsMiddleware:
    """Counts every response by route and status, and times every request.
    A route that never matched (404, or an error before routing) is
    labeled ``unmatched`` rather than the raw path, to keep cardinality
    bounded."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter()
        status_holder: list[int] = []

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder.append(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if not status_holder:
                status_holder.append(500)
            raise
        finally:
            route = scope.get("route")
            route_label = getattr(route, "path", UNMATCHED_ROUTE)
            status = str(status_holder[0]) if status_holder else "500"
            HTTP_REQUESTS_TOTAL.labels(route=route_label, status=status).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(route=route_label).observe(
                time.perf_counter() - started_at
            )
