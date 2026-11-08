import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from shc_api.config import get_settings
from shc_api.models import Base

target_metadata = Base.metadata


def do_run(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async():
    url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(do_run)
    await engine.dispose()


def run_sync_entry():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_async())
        return
    # Called from inside a running loop (tests): run in a fresh thread.
    import threading

    t = threading.Thread(target=lambda: asyncio.run(run_async()))
    t.start()
    t.join()


run_sync_entry()
