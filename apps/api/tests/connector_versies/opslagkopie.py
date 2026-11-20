"""Een wachtende opdracht op een fysieke terugvalkopie van de direct voorgaande versie."""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from postgres import draai_postgres
from versie import andere_versie

from shc_api import cli
from shc_api.config import get_settings
from tests.test_connector_herstel import _apply, _queue_rollback
from tests.test_connector_routes import proef as proef
from tests.test_inbedrijfstelling_migratie import sql


@pytest.mark.parametrize("route", ["onderhoud", "installatie"])
@pytest.mark.parametrize("richting", ["rollback", "apply"])
async def test_snapshot_met_bijbehorende_vorige_software(proef, tmp_path, route, richting):
    bron = get_settings().database_url
    adres = urlsplit(cli._libpq(bron)[0])
    assert adres.hostname == "localhost" and adres.path.startswith("/shc_t_")
    assert urlunsplit(adres._replace(path="")) == os.environ["PROEF_OPSLAG_PG"]
    assert await sql(bron, "SHOW data_directory") == [(str(Path(os.environ["TMPDIR"]) / "bron"),)]
    assert cli.current_revision(bron) == "0008"

    if richting == "rollback":
        plan, _ = await _apply(proef)
        opdracht = await _queue_rollback(proef, plan)
    else:
        plan = await proef.propose()
        antwoord = await proef.post("apply", proef.request(preview=plan))
        assert antwoord.status_code == 202
        opdracht = antwoord.json()["command_id"]
    ident = {"id": uuid.UUID(opdracht)}
    sleutel = {"id": uuid.UUID(plan["proposal_id"])}
    rij = "SELECT status, attempts, detail FROM commands WHERE id=:id"
    bewijs = 'SELECT "before", preview_hash, command_id, rollback_command_id FROM connector_changes WHERE id=:id'
    oud = await sql(bron, bewijs, sleutel)
    assert oud[0][:2] == (plan["before"], plan["preview_hash"])
    assert await sql(bron, "SELECT status, attempts FROM commands WHERE id=:id", ident) == [("queued", 0)]
    aantal, stand = len(proef.unit.commands), proef.unit.state["SetTem"]

    data = tmp_path / "kopie"
    await asyncio.to_thread(subprocess.run, ["pg_basebackup", "--dbname", adres.geturl(), "-D", str(data),
                            "--checkpoint=fast", "--wal-method=stream", "--manifest-checksums=SHA256"], check=True)
    await asyncio.to_thread(subprocess.run, ["pg_verifybackup", str(data)], check=True)
    start = {"actie": "migreer", "map": str(tmp_path / "upgrade")}
    assert await andere_versie(start) == {"schema": "0009", "geweigerd": False, "archief": True}
    verwacht = [("cancelled", 0, "versiewissel")]
    assert await sql(bron, rij, ident) == verwacht
    assert await sql(bron, bewijs, sleutel) == oud

    with draai_postgres(data, tmp_path / "kopie.log", os.environ) as poort:
        kopie = urlunsplit(urlsplit(bron)._replace(netloc=f"postgres@localhost:{poort}"))
        assert (await sql(kopie, rij, ident))[0][:2] == ("queued", 0)
        assert await sql(kopie, bewijs, sleutel) == oud
        if route == "installatie":
            # De formulierbewaker voert zijn vaste startopdracht uit op de PostgreSQL van deze kloon.
            repo = Path(os.environ["PROEF_EINDVERSIE"])
            paden = [repo / naam for naam in ("apps/api/src", "integrations/src")]
            env = dict(os.environ, SHC_DATABASE_URL=kopie, PYTHONPATH=os.pathsep.join(map(str, paden)))
            await asyncio.to_thread(subprocess.run, [sys.executable, "-c",
                                    "import os; from deploy.allinone.supervisor import Supervisor; "
                                    "Supervisor(dict(os.environ)).prepare_fallback()"], cwd=repo, env=env, check=True)
        else:
            assert await andere_versie({"actie": "sluit_kopie"}, url=kopie) == {"schema": "0008"}
        vorige = await andere_versie(start, versie="direct_vorig", url=kopie)
        assert vorige["schema"] == "0008" and not vorige["geweigerd"]
        werk = {"actie": "werk", "woning": str(proef.home_id), "controleer_start": True}
        assert await andere_versie(werk, versie="direct_vorig", url=kopie) == {"geblokkeerd": False}
        assert await sql(kopie, bewijs, sleutel) == oud
        assert await sql(kopie, rij, ident) == verwacht
        assert await sql(kopie, "SELECT count(*) FROM idempotency_keys WHERE key=:id", sleutel) == [(0,)]
        meldingen = await sql(kopie, """SELECT count(*) FROM alerts WHERE key=:key AND home_id=:woning
                            AND title='Handmatige controle nodig'""",
                             {"key": "connector:rollback:" + opdracht, "woning": proef.home_id})
        assert meldingen == [(int(richting == "rollback"),)]
    assert (len(proef.unit.commands), proef.unit.state["SetTem"]) == (aantal, stand)
    print(f"OPSLAGKOPIE {richting}-{route} claims=0 verzendingen=0 stand={stand} meldingen={meldingen[0][0]}")
