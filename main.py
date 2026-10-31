"""Application entry point: ``python main.py API``."""

import argparse

import uvicorn

from components.core.config import get_settings
from components.core.logging import configure_logging


def run_api() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        # Without this, uvicorn's own dictConfig gives "uvicorn" and
        # "uvicorn.access" their own plain-text handlers with
        # propagate=False, so their lines never reach the JSON handler
        # above. log_config=None skips that; the loggers then have no
        # handler of their own and propagate to the JSON-configured root.
        log_config=None,
    )


COMMANDS = {"API": run_api}


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM Arena")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args()
    COMMANDS[args.command]()


if __name__ == "__main__":
    main()
