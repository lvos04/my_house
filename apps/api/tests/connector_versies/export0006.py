"""Maak met de oude applicatiecode op fbaebb37 een logische export van uitsluitend de eigen proefdatabase."""

import asyncio
import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from tests.test_ws_backup_format import PW


async def export():
    url = os.environ["SHC_DATABASE_URL"]
    delen = urlsplit(url)
    assert delen.hostname == "localhost" or ipaddress.ip_address(delen.hostname).is_loopback
    assert delen.path.startswith("/shc_tmp_")
    assert bk.head_revision() == "0006"
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            manifest = await bk.write_backup(conn, Path(sys.argv[1]), PW, include_secrets=False)
    finally:
        await engine.dispose()
    assert manifest["schema_revision"] == "0006" and "connector_changes" not in manifest["tables"]


if __name__ == "__main__":
    asyncio.run(export())
