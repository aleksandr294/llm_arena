"""Serve the app with uvicorn on the test's schema (for real-socket tests).

Usage: ``python -m tests.admin.uvicorn_child PORT``.
"""

import sys

import uvicorn

from tests.admin.panel_process import route_to_worker_schema

if __name__ == "__main__":
    route_to_worker_schema()
    uvicorn.run(
        "api.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=int(sys.argv[1]),
        log_level="warning",
    )
