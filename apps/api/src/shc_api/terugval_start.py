#!/opt/venv/bin/python -I
"""Startbestand voor de historische image, alleen-lezen gekoppeld over haar CLI."""

import asyncio
import os
import stat
import sys
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api.config import get_settings


class KopieOnbekend(ValueError):
    pass


async def opslagidentiteit(conn, *, voorbereiden=False):
    # De catalogus wordt meegekopieerd door pg_basebackup. Het bestand van de daadwerkelijk
    # aangekoppelde opslag heeft op de nieuwe kopie een andere combinatie van device en inode.
    bestand = Path("/run/shc-terugval-pgversie")
    try:
        try:
            info = await asyncio.to_thread(bestand.lstat)
        except FileNotFoundError:
            mapnaam = await conn.scalar(text("SHOW data_directory"))
            info = await asyncio.to_thread((Path(mapnaam) / "PG_VERSION").lstat)
        if stat.S_ISREG(info.st_mode):
            if voorbereiden:
                grootte, tijd = (await conn.execute(text("""SELECT size, floor(extract(epoch FROM modification))::bigint
                                                    FROM pg_stat_file('PG_VERSION')"""))).one()
                if info.st_size != grootte or info.st_mtime_ns // 1_000_000_000 != tijd:
                    raise KopieOnbekend("De bestandsgegevens van PG_VERSION wijken af van de aangesloten database.")
            return f"{info.st_dev}:{info.st_ino}"
    except (OSError, SQLAlchemyError):
        pass
    raise KopieOnbekend("Koppel PG_VERSION van de offline PostgreSQL-kopie alleen-lezen aan "
                        "/run/shc-terugval-pgversie. Gebruik voor de voorbereiding de beheerrol van die kopie.")


async def prepared(receipt):
    # Lees uit de catalogus van de werkelijk geopende database. Een clientoptie
    # of een ontvangstbewijs in een andere database kan deze controle niet invullen.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool, hide_parameters=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended('shc-terugval', 0))"))
            storage = await opslagidentiteit(conn)
            geldig = await conn.scalar(text("""
                SELECT EXISTS (SELECT FROM pg_catalog.pg_db_role_setting s
                    JOIN pg_catalog.pg_database d ON d.oid=s.setdatabase
                    CROSS JOIN public.alembic_version v
                    WHERE d.datname=current_database() AND s.setrole=0
                      AND ('shc.terugval=' || :id || ':' || v.version_num || ':' || :storage) = ANY(s.setconfig))
                AND NOT EXISTS (SELECT FROM public.commands WHERE source='connector'
                                AND status IN ('queued', 'sending', 'sent'))
            """), {"id": str(receipt), "storage": storage})
            if geldig and await conn.scalar(text("SELECT to_regclass('public.connector_changes') IS NOT NULL")):
                return not await conn.scalar(text("""SELECT EXISTS (SELECT FROM public.idempotency_keys
                    WHERE scope='connector:voorstel' OR scope LIKE 'connector:voorstel:%')"""))
            return geldig
    finally:
        await engine.dispose()


def main():
    try:
        receipt = uuid.UUID(os.environ.get("SHC_TERUGVAL_ID", ""))
        if sys.argv[1:] == ["terugval-app"]:
            from shc_allinone import supervisor

            if supervisor.check_layout(supervisor.DATA):
                raise ValueError
            # De gewone historische bewaker opent PostgreSQL. Zijn migrate, serve
            # en worker gaan vervolgens alle drie door ditzelfde gekoppelde bestand.
            return supervisor.main()
        if not os.environ.get("SHC_DATABASE_URL") or not asyncio.run(prepared(receipt)):
            raise ValueError
    except Exception:
        print("Terugval geweigerd. Controleer de aangekoppelde kopie en het Terugval-ID uit de "
              "geslaagde voorbereiding. De oude worker blijft uit.", file=sys.stderr)
        return 2

    from shc_api.cli import main as historical_cli

    args = sys.argv[1:]
    if args[:1] == ["terugval"]:
        args = args[1:]
    return historical_cli(args)


if __name__ == "__main__":
    sys.exit(main())
