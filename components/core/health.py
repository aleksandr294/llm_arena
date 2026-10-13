import asyncio
from collections.abc import Awaitable, Callable

import aio_pika
from sqlalchemy import text

from components.core.config import get_settings
from components.core.database import get_engine

HealthCheck = Callable[[], Awaitable[None]]


async def check_database() -> None:
    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_rabbitmq() -> None:
    url = get_settings().rabbitmq_url.get_secret_value()
    connection = await aio_pika.connect(url)
    await connection.close()


async def run_check(check: HealthCheck, timeout_seconds: float) -> str:
    """Return ``ok`` or ``failing``. Errors are not echoed back: they may
    contain connection strings."""
    try:
        async with asyncio.timeout(timeout_seconds):
            await check()
    except Exception:
        return "failing"
    return "ok"
