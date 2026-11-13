"""Alembic environment.

Three ways in:
- ``config.attributes["connection"]``: a (sync) connection supplied by the caller, e.g. a restore
  that downgrades, loads data and upgrades again inside ONE transaction. Migrations then run in
  that transaction and nothing is committed here.
- ``sqlalchemy.url`` main option: migrate another database (restore drill into a temporary DB).
- otherwise the app database from settings.

Errors always propagate to the caller (also when migrations run in a helper thread), so a failed
migration stops start-up with a clear message instead of being swallowed.
"""

import asyncio
import threading

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

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
    engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(do_run)
            await conn.commit()
    finally:
        await engine.dispose()


def run_sync_entry():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_async())
        return
    # Called from inside a running loop (tests, API): run in a fresh thread and re-raise its error.
    errors: list[BaseException] = []

    def target():
        try:
            asyncio.run(run_async())
        except BaseException as exc:  # noqa: BLE001 - re-raised below in the caller's thread
            errors.append(exc)

    t = threading.Thread(target=target, name="alembic-migrate")
    t.start()
    t.join()
    if errors:
        raise errors[0]


_external = context.config.attributes.get("connection")
if _external is not None:
    do_run(_external)
else:
    run_sync_entry()
