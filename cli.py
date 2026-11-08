"""Operator commands: ``python cli.py create-superadmin --email <email>`` and
``python cli.py purge-refresh-tokens``.

The password is read from a hidden prompt or ``SUPERADMIN_PASSWORD``, never
from an argument, and is never printed.
"""

import asyncio
import os
from datetime import timedelta
from typing import Annotated

import typer

from components.auth.dependencies import build_auth_service
from components.auth.exceptions import PasswordPolicyError
from components.core.config import get_settings
from components.core.database import session_scope
from components.users.dependencies import build_user_service
from components.users.exceptions import DuplicateEmailError, InvalidEmailError

app = typer.Typer(add_completion=False, no_args_is_help=True)

PASSWORD_ENV = "SUPERADMIN_PASSWORD"  # noqa: S105 - name of an env variable, not a password


@app.callback()
def main() -> None:
    """Operator commands for LLM Arena."""


async def _create_superadmin(email: str, password: str) -> str:
    async with session_scope() as session:
        service = build_user_service(
            session, build_auth_service(session, get_settings())
        )
        user = await service.create_superadmin(email, password)
        return str(user.id)


@app.command("create-superadmin")
def create_superadmin(
    email: Annotated[str, typer.Option(help="Email of the new superadmin.")],
) -> None:
    """Create a superadmin and print its id."""
    password = os.environ.get(PASSWORD_ENV)
    if not password:
        password = typer.prompt(
            "Password", hide_input=True, confirmation_prompt=True
        )
    try:
        user_id = asyncio.run(_create_superadmin(email, password))
    except (InvalidEmailError, PasswordPolicyError, DuplicateEmailError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(user_id)


async def _purge(retention: timedelta) -> tuple[int, int]:
    async with session_scope() as session:
        service = build_auth_service(session, get_settings())
        return await service.purge(retention)


@app.command("purge-refresh-tokens")
def purge_refresh_tokens(
    retention_days: Annotated[
        int,
        typer.Option(
            min=0,
            help="Keep tokens that expired less than this many days ago.",
        ),
    ] = 7,
) -> None:
    """Delete long-expired refresh tokens and stale sign-in throttle rows.

    Run it periodically (cron, a scheduled job): nothing else removes them.
    """
    tokens, throttles = asyncio.run(_purge(timedelta(days=retention_days)))
    typer.echo(
        f"Deleted {tokens} refresh token(s) and {throttles} throttle row(s)"
    )


if __name__ == "__main__":
    app()
