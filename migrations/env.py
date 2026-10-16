import asyncio

from alembic import context
from sqlalchemy.engine import Connection

# Import every component's models module here so its tables are registered
# on Base.metadata and visible to autogenerate.
import components.datasets.models  # noqa: F401
import components.evaluation.models  # noqa: F401
import components.experiments.models  # noqa: F401
from components.core.config import get_settings
from components.core.database import Base, create_engine

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
