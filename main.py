"""Application entry point: ``python main.py API``."""

import argparse

import uvicorn

from components.core.config import get_settings


def run_api() -> None:
    settings = get_settings()
    uvicorn.run(
        "api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
    )


COMMANDS = {"API": run_api}


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM Arena")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args()
    COMMANDS[args.command]()


if __name__ == "__main__":
    main()
