"""Gebruik één opgegeven versie met de lopende, uitsluitend lokale proefdatabase en simulator."""

import asyncio
import ipaddress
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from shc_integrations.gree import GreeAdapter
from sqlalchemy import select

from shc_api import backup, cli
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.errors import require_secret_box
from shc_api.main import create_app
from shc_api.models import Integration
from shc_api.worker import Worker
from shc_api.worker.core import wait_while_schema_newer


async def andere_versie(invoer, versie=None, url=None):
    bron = Path(os.environ["PROEF_VERSIES"]) / versie if versie else Path(os.environ["PROEF_EINDVERSIE"])
    env = dict(os.environ, BRON_VAN_DE_PROEF=str(bron), SHC_DATABASE_URL=url or get_settings().database_url)
    env["PYTHONPATH"] = os.pathsep.join(str(bron / deel) for deel in ("apps/api/src", "integrations/src", "apps/api"))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, __file__, cwd=bron / "apps/api",
        env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    uitvoer, _ = await proc.communicate(json.dumps(invoer).encode())
    assert proc.returncode == 0, "Het afzonderlijke lokale applicatieproces kon de proef niet uitvoeren."
    return json.loads(uitvoer)


async def meet(invoer):
    bron = Path(os.environ["BRON_VAN_DE_PROEF"])
    assert Path(cli.__file__).is_relative_to(bron)
    url = get_settings().database_url
    delen = urlsplit(url)
    assert delen.hostname == "localhost" or ipaddress.ip_address(delen.hostname).is_loopback
    assert delen.path.startswith(("/shc_t_", "/shc_tmp_"))
    if invoer["actie"] == "migreer":
        geweigerd = False
        pad = None
        try:
            pad = await asyncio.to_thread(cli.migrate, url, backup_dir=Path(invoer["map"]))
        except cli.MigrationError as fout:
            if "vorige installatie" not in str(fout):
                raise
            geweigerd = True
        if pad:
            await asyncio.to_thread(cli.verify_migration_dump, pad)
        return {"geweigerd": geweigerd, "schema": cli.current_revision(url), "archief": bool(pad)}
    if invoer["actie"] == "herstel_export":
        from tests.test_ws_backup_format import PW
        rapport = await backup.restore_file(Path(invoer["pad"]), PW, url=url)
        return {"schema": rapport.schema_to}
    if invoer["actie"] == "dump":
        from tests.test_inbedrijfstelling_migratie import sql
        from tests.test_ws_backup_format import PW, temp_db

        mapnaam = Path(invoer["map"])
        try:
            record = await backup.run_db_dump(PW, auto=False, url=url, backup_dir=mapnaam)
        except backup.BackupError as fout:
            if "connectorherstelbewijs" not in str(fout):
                raise
            return {"geweigerd": True}
        plain = mapnaam / "rijcontrole.pgdump"
        await asyncio.to_thread(backup.decrypt_dump, mapnaam / record["filename"], PW, plain)
        async with temp_db(None) as doel:
            await asyncio.to_thread(subprocess.run, ["pg_restore", "--single-transaction", "--dbname",
                                    cli._libpq(doel)[0], str(plain)], check=True, capture_output=True)
            rijen = await sql(doel, """
                SELECT cmd.status, c.reason_code, c.before->>'target_c', cmd.id::text, c.before,
                       (SELECT count(*) FROM connector_hub_pins)
                FROM connector_changes c JOIN commands cmd ON cmd.id = c.command_id WHERE c.id=:id
            """, {"id": uuid.UUID(invoer["voorstel"])})
        return {"geweigerd": False, "rijen": [list(rij) for rij in rijen]}
    if invoer["actie"] == "werk":
        woning = uuid.UUID(invoer["woning"])
        if invoer.get("controleer_start"):
            try:
                await asyncio.wait_for(wait_while_schema_newer(), timeout=3)
            except TimeoutError:
                return {"geblokkeerd": True}
        worker = Worker()
        async with sessionmaker()() as db:
            koppeling = (await db.scalars(select(Integration).where(Integration.home_id == woning))).one()
            assert koppeling.provider == "gree" and ipaddress.ip_address(koppeling.config["host"]).is_loopback
            worker.adapters[koppeling.id] = GreeAdapter(
                koppeling.config, require_secret_box().decrypt(koppeling.credentials_enc),
            )
        await worker.process_commands({woning})
        return {"geblokkeerd": False}
    assert invoer["actie"] == "route"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test", cookies=invoer["sessie"],
    ) as client:
        antwoord = await client.request(
            invoer["methode"], "/connector/v1/" + invoer["route"],
            headers=invoer["koppen"], json=invoer.get("verzoek"),
        )
        return {"http": antwoord.status_code, "inhoud": antwoord.json()}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(meet(json.load(sys.stdin)))))
