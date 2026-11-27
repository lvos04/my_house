"""Volledige .shcbak met een wachtende rollback over een software- of schemawissel."""

import asyncio
import hashlib
import json
import os
import uuid
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from versie import EINDSCHEMA, andere_versie

from shc_api import backup, cli
from shc_api.config import get_settings
from shc_api.connector import RESTORED_COMMAND_DETAIL
from tests.test_connector_herstel import _apply, _queue_rollback
from tests.test_connector_routes import proef as proef
from tests.test_inbedrijfstelling_migratie import sql
from tests.test_ws_backup_format import PW, temp_db

METING = """
    SELECT cmd.status, cmd.attempts, cmd.detail, c.rollback_after IS NULL, jsonb_typeof(c.rollback_after),
           (SELECT count(*) FROM alerts WHERE key='connector:rollback:' || cmd.id AND title='Handmatige controle nodig'),
           EXISTS (SELECT FROM idempotency_keys WHERE key=c.id)
    FROM connector_changes c JOIN commands cmd ON c.rollback_command_id=cmd.id WHERE c.id=:id
"""
BEWIJS = 'SELECT "before", preview_hash, command_id::text, rollback_command_id::text FROM connector_changes WHERE id=:id'


def archiefbewijs(pad, voorstel, formaat):
    bewijs = []
    for naam, deel in backup.iter_members(pad, PW):
        if backup._table_of(naam) == "connector_changes":
            for regel in backup._lines(deel):
                rij = json.loads(regel)
                if rij["id"] == voorstel:
                    # In formaat 3 staat nullable JSONB in PostgreSQL-tekstnotatie.
                    voor = json.loads(rij["before"]) if formaat == 3 else rij["before"]
                    assert rij["rollback_after"] is None
                    bewijs.append((voor, rij["preview_hash"], rij["command_id"], rij["rollback_command_id"]))
    return bewijs


async def import_en_herstart(pad, url, voorstel):
    sleutel = {"id": uuid.UUID(voorstel)}
    controle = cli._check_connector_rows
    waarnemingen = []

    def observeer(conn, *args, **kwargs):
        rij = conn.execute(text(METING), sleutel).first()
        if rij is not None:
            waarnemingen.append(list(rij))
        return controle(conn, *args, **kwargs)

    stap = "import"
    with patch.object(cli, "_check_connector_rows", observeer):
        try:
            await backup.restore_file(pad, PW, url=url)
            stap = "herstart"
            await asyncio.to_thread(cli.migrate, url, backup_dir=pad.parent / "herstart")
        except (cli.MigrationError, backup.BackupError) as fout:
            if "connectorherstelbewijs" not in str(fout) or not waarnemingen:
                raise
            return {"geweigerd": stap, "meting": waarnemingen[-1]}
    return {"geweigerd": None, "meting": list((await sql(url, METING, sleutel))[0])}


async def test_versiewissel_met_logische_export(proef, tmp_path, monkeypatch):
    bron = get_settings().database_url
    schema = cli.current_revision(bron)
    assert schema in {"0008", EINDSCHEMA}
    wissel = os.environ["PROEF_LOGISCHE_WISSEL"]
    if wissel == "versienummer":
        monkeypatch.setattr(backup, "VERSION", backup.VERSION + "+andere-versie")
    if wissel == "bouw":
        from shc_api import connector_changes, connector_transition
        ander = connector_changes.LOCAL_PROPOSAL + "-andere-versie"
        monkeypatch.setattr(connector_changes, "LOCAL_PROPOSAL", ander)
        monkeypatch.setattr(connector_transition, "LOCAL_PROPOSAL", ander)
    plan, opdracht = await _apply(proef)
    rollback = await _queue_rollback(proef, plan)
    sleutel = {"id": uuid.UUID(plan["proposal_id"])}
    verwacht = [(plan["before"], plan["preview_hash"], opdracht, rollback)]
    assert await sql(bron, BEWIJS, sleutel) == verwacht
    bronrij = (await sql(bron, METING, sleutel))[0]
    assert bronrij[:2] == ("queued", 0) and bronrij[3:5] == (True, None)
    pad = tmp_path / "versiewissel.shcbak"
    engine = create_async_engine(bron, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            manifest = await backup.write_backup(conn, pad, PW, include_secrets=True)
            if wissel == "versienummer":
                assert manifest["app_version"].endswith("+andere-versie")
    finally:
        await engine.dispose()
    archief = await asyncio.to_thread(backup.verify_file, pad, PW)
    sha = hashlib.sha256(pad.read_bytes()).digest()
    assert archiefbewijs(pad, plan["proposal_id"], archief.manifest["format_version"]) == verwacht

    async with temp_db(None) as doel:
        assert (await andere_versie({"actie": "migreer", "map": str(tmp_path / "doel")}, url=doel))["schema"] == EINDSCHEMA
        await sql(doel, "INSERT INTO settings(key,value) VALUES ('proef_logische_meting','true')")
        resultaat = await andere_versie({"actie": "meet_logische_export", "pad": str(pad), "voorstel": plan["proposal_id"]},
                                        url=doel)
        geweigerd = resultaat["geweigerd"]
        markering = await sql(doel, "SELECT count(*) FROM settings WHERE key='proef_logische_meting'")
        assert markering == [(int(geweigerd == "import"),)]
        assert await sql(doel, BEWIJS, sleutel) == ([] if geweigerd == "import" else verwacht)
        if geweigerd is None:
            werk = {"actie": "werk", "woning": str(proef.home_id), "controleer_start": True}
            assert await andere_versie(werk, url=doel) == {"geblokkeerd": False}
            assert tuple(resultaat["meting"]) == (await sql(doel, METING, sleutel))[0]
        status, claims, reden, sqlnull, jsontype, melding, recht = resultaat["meting"]
    assert await sql(bron, BEWIJS, sleutel) == verwacht
    assert (await sql(bron, METING, sleutel))[0] == bronrij
    assert hashlib.sha256(pad.read_bytes()).digest() == sha
    assert len(proef.unit.commands) == 1 and proef.unit.state["SetTem"] == 22
    assert status == "cancelled" and claims == 0 and not recht
    print(f"LOGISCHE_EXPORT {schema}->{EINDSCHEMA} geweigerd={geweigerd} SQL_NULL={sqlnull} json_type={jsontype} "
          f"reden={reden} melding={melding} claims={claims} extra_verzendingen=0")
    assert geweigerd is None and sqlnull and jsontype is None
    if wissel == "ongewijzigd":
        assert reden == RESTORED_COMMAND_DETAIL and melding == 0
    else:
        assert reden == "versiewissel" and melding == 1
