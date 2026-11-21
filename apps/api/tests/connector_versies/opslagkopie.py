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
from versie import EINDSCHEMA, andere_versie

from shc_api import cli
from shc_api.config import get_settings
from tests.test_connector_herstel import _apply, _queue_rollback
from tests.test_connector_routes import proef as proef
from tests.test_inbedrijfstelling_migratie import sql


def omgeving(repo, url, tmp_path):
    paden = [repo / deel for deel in ("apps/api/src", "integrations/src")]
    return dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, paden)), SHC_DATABASE_URL=url,
                SHC_WORKER_ALIVE_FILE=str(tmp_path / "hartslag"), SHC_DATA_DIR=str(tmp_path / "app"),
                SHC_BACKUP_DIR=str(tmp_path / "backups"), SHC_NTP_SERVER="")


async def voer_uit(args, env, cwd):
    return await asyncio.to_thread(subprocess.run, args, cwd=cwd, env=env, capture_output=True,
                                   text=True, timeout=45)


async def workerstart(startbestand, argumenten, env, *, vrij):
    hartslag = Path(env["SHC_WORKER_ALIVE_FILE"])
    assert not await asyncio.to_thread(hartslag.exists)
    proc = await asyncio.create_subprocess_exec(sys.executable, str(startbestand), *argumenten, "worker",
                                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with asyncio.timeout(45):
            while proc.returncode is None and not await asyncio.to_thread(hartslag.exists):
                await asyncio.sleep(0.1)
        assert await asyncio.to_thread(hartslag.exists) == vrij
        if not vrij:
            assert proc.returncode == 2
    finally:
        if proc.returncode is None:
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except TimeoutError:
            proc.kill()
            await proc.wait()


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
    assert await andere_versie(start) == {"schema": EINDSCHEMA, "geweigerd": False, "archief": True}
    verwacht = [("cancelled", 0, "versiewissel")]
    assert await sql(bron, rij, ident) == verwacht
    assert await sql(bron, bewijs, sleutel) == oud

    with draai_postgres(data, tmp_path / "kopie.log", os.environ) as poort:
        kopie = urlunsplit(urlsplit(bron)._replace(netloc=f"postgres@localhost:{poort}"))
        wachtrij = await sql(kopie, rij, ident)
        assert wachtrij[0][:2] == ("queued", 0)
        assert await sql(kopie, bewijs, sleutel) == oud
        repo = Path(os.environ["PROEF_EINDVERSIE"])
        oud_repo = Path(os.environ["PROEF_VERSIES"]) / "direct_vorig"
        historisch = omgeving(oud_repo, kopie, tmp_path)
        historisch.pop("SHC_TERUGVAL_ID", None)
        startbestand = repo / "apps/api/src/shc_api/terugval_start.py"
        argumenten = [] if route == "installatie" else ["terugval"]
        env = omgeving(repo, kopie, tmp_path)
        if route == "installatie":
            # De blijvende formulierinstelling werkt ook vóór het koppelen van het aparte startbestand.
            # Geef de ongewijzigde historische bewaker dezelfde pakketnaam als in zijn image.
            assert (oud_repo / "deploy/allinone/supervisor.py").is_file()
            (tmp_path / "shc_allinone").symlink_to(oud_repo / "deploy/allinone", target_is_directory=True)
            vorm = await voer_uit([sys.executable, "-m", "deploy.allinone.startpad"], env, repo)
            assert vorm.returncode == 0 and vorm.stdout.strip().startswith("shc_allinone.start_")
            tagwissel = await voer_uit([sys.executable, "-m", vorm.stdout.strip()], historisch, tmp_path)
            assert tagwissel.returncode != 0 and "No module named " + vorm.stdout.strip() in tagwissel.stderr
        await workerstart(startbestand, argumenten, historisch, vrij=False)
        assert await sql(kopie, rij, ident) == wachtrij
        assert await sql(kopie, bewijs, sleutel) == oud
        assert (len(proef.unit.commands), proef.unit.state["SetTem"]) == (aantal, stand)
        if route == "installatie":
            # Bij een onzekere verzending mag ook een poging tot voorbereiding geen vrijgave opleveren.
            await sql(kopie, "UPDATE commands SET status='sending', attempts=1 WHERE id=:id", ident)
            onzeker = await sql(kopie, rij, ident)
            poging = await voer_uit(["shc-api", "prepare-connector-fallback"], env, repo)
            assert poging.returncode == 2 and "Terugval-ID:" not in poging.stdout
            await workerstart(startbestand, argumenten, dict(historisch, SHC_TERUGVAL_ID=str(uuid.uuid4())), vrij=False)
            assert await sql(kopie, rij, ident) == onzeker and await sql(kopie, bewijs, sleutel) == oud
            assert (len(proef.unit.commands), proef.unit.state["SetTem"]) == (aantal, stand)
            await sql(kopie, "UPDATE commands SET status='queued', attempts=0 WHERE id=:id", ident)
        if route == "installatie":
            # De formulierbewaker voert zijn vaste startopdracht uit op de PostgreSQL van deze kloon.
            voorbereid = await voer_uit([sys.executable, "-c",
                                         "import os; from deploy.allinone.supervisor import Supervisor; "
                                         "Supervisor(dict(os.environ)).prepare_fallback()"], env, repo)
        else:
            voorbereid = await voer_uit(["shc-api", "prepare-connector-fallback"], env, repo)
        assert voorbereid.returncode == 0
        regel, = [s for s in voorbereid.stdout.splitlines() if s.startswith("Terugval-ID: ")]
        historisch["SHC_TERUGVAL_ID"] = str(uuid.UUID(regel.removeprefix("Terugval-ID: ")))
        if richting == "apply" and route == "installatie":
            dubbel = tmp_path / "na-voorbereiding"
            await asyncio.to_thread(subprocess.run, ["pg_basebackup", "--dbname", cli._libpq(kopie)[0],
                                    "-D", str(dubbel), "--checkpoint=fast", "--manifest-checksums=SHA256"], check=True)
            await asyncio.to_thread(subprocess.run, ["pg_verifybackup", str(dubbel)], check=True)
            with draai_postgres(dubbel, tmp_path / "dubbel.log", os.environ) as andere_poort:
                ander = urlunsplit(urlsplit(bron)._replace(netloc=f"postgres@localhost:{andere_poort}"))
                assert await sql(ander, "SHOW shc.terugval") == await sql(kopie, "SHOW shc.terugval")
                await workerstart(startbestand, argumenten, dict(historisch, SHC_DATABASE_URL=ander), vrij=False)
                assert await sql(ander, rij, ident) == verwacht and await sql(ander, bewijs, sleutel) == oud
        # Het afgegeven ID is ook op een andere leesbare database met lege wachtrij ongeldig.
        verwisseld = dict(historisch, SHC_DATABASE_URL=bron)
        await workerstart(startbestand, argumenten, verwisseld, vrij=False)
        geweigerd = await voer_uit([sys.executable, str(startbestand), *argumenten, "migrate"], verwisseld, oud_repo)
        assert geweigerd.returncode == 2 and "Terugval geweigerd" in geweigerd.stderr
        assert await sql(bron, bewijs, sleutel) == oud
        assert await sql(bron, rij, ident) == verwacht
        migratie = await voer_uit([sys.executable, str(startbestand), *argumenten, "migrate"], historisch, oud_repo)
        assert migratie.returncode == 0
        vorige = await andere_versie(start, versie="direct_vorig", url=kopie)
        assert vorige["schema"] == "0008" and not vorige["geweigerd"]
        await workerstart(startbestand, argumenten, historisch, vrij=True)
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
    print(f"OPSLAGKOPIE {richting}-{route} zonder_id=uit verwisselde_kopie=uit voorbereid=hartslag "
          f"claims=0 verzendingen=0 stand={stand} meldingen={meldingen[0][0]}")
