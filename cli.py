"""Operator commands: ``python cli.py create-superadmin --email <email>``.

The password is read from a hidden prompt or ``SUPERADMIN_PASSWORD``, never
from an argument, and is never printed.
"""

import asyncio
import os
from typing import Annotated

import typer

from components.auth.security import PasswordPolicyError
from components.core.database import get_session_factory
from components.users.service import (
    DuplicateEmailError,
    InvalidEmailError,
    UserService,
)

app = typer.Typer(add_completion=False, no_args_is_help=True)

PASSWORD_ENV = "SUPERADMIN_PASSWORD"


@app.callback()
def main() -> None:
    """Operator commands for LLM Arena."""


async def _create_superadmin(email: str, password: str) -> str:
    async with get_session_factory()() as session:
        user = await UserService(session).create_superadmin(email, password)
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


if __name__ == "__main__":
    app()
