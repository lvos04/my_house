"""S-P3-700 (my_house-bouwlijst P3-09, EX15, B06-008; ADR-0004 besluit 9): een database van een nieuwere my_house-versie.

Op 2026-11-11 maakte ``cli.migrate`` op een database met een onbekende revisie bij elke start eerst een volledige
``pg_dump`` en stopte dan met een alembic ``CommandError`` (DELTA_MATRIX.md, kop "11.8", probe c: drie starts, drie
dumps). Met ``restart: unless-stopped`` werd dat een herstartlus die de opslag volschrijft. Nu:

(a) een revisie die niet in de eigen migratieketen staat (``backup.schema_relation`` = "newer") wordt herkend VOOR de
    dump: geen dump, geen migratie, geen schrijfactie; ``migrate`` geeft de eigen fout ``SchemaNewerError``;
(b) ``shc-api serve`` start dan de herstelmodus: alleen ``/api/v1/health`` (503, ``herstelmodus``) en de herstelinfo
    ``/api/v1/system/recovery`` (200); elke andere route, ook elke schrijvende, geeft 503 ``database_newer``;
    de worker start geen rondes en wacht zonder te stoppen;
(c) een database op het huidige schema start gewoon;
(d) een gevulde database op een ouder schema migreert zoals voorheen, met een dump die de gegevens bevat (teruggezet
    in een lege database: dezelfde inhoud als vóór de migratie).

De tabellen worden vergeleken met inhoud (niet leeg, V-003); de herkenning gebruikt de migratieketen, niet de letterlijke
waarde "0099" (V-086: ook het eerstvolgende nummer telt als nieuwer).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import gzip
import json
import logging
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api import cli
from shc_api.config import VERSION

from .test_ws_backup_format import temp_db

TEKST = (
    "De database is van een nieuwere my_house-versie; kies in het formulier van de Custom App de bijpassende versie "
    "of zet de snapshot van vóór de update terug."
)
NIEUWER = ["0099_toekomst", f"{int(bk.head_revision()) + 1:04d}"]
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")


# --- hulpmiddelen ---------------------------------------------------------------------------------------------------


async def _vul(url: str) -> None:
    """Gegevens in een database op het huidige schema: gebruiker, woning en instelling (V-003: niet leeg)."""
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO users (id, username, password_hash, role, disabled, created_at)"
                " VALUES (:i, 'bewoner', 'x', 'admin', false, now())"
            ),
            {"i": uuid.uuid4()},
        )
        await c.execute(
            text("INSERT INTO homes (id, name, timezone, currency, active_from) VALUES (:h, 'Proef', 'UTC', 'EUR', now())"),
            {"h": uuid.uuid4()},
        )
        await c.execute(text("""INSERT INTO settings (key, value) VALUES ('proef_s_p3_700', '{"a": 1}'::jsonb)"""))
    await engine.dispose()


async def _zet_revisie(url: str, revisie: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as c:
        await c.execute(text("UPDATE alembic_version SET version_num = :r"), {"r": revisie})
    await engine.dispose()


async def _inhoud(url: str, *, zonder_nieuwe_gate: bool = False) -> dict[str, tuple[int, str]]:
    """Per tabel (ook alembic_version): aantal rijen en een md5 over alle rijen."""
    engine = create_async_engine(url, poolclass=NullPool)
    out: dict[str, tuple[int, str]] = {}
    async with engine.connect() as c:
        tabellen = (
            await c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"))
        ).scalars()
        for t in list(tabellen):
            # tabelnamen uit pg_tables van de eigen proefdatabase
            where = " WHERE key <> 'commissioning_gate'" if zonder_nieuwe_gate and t == "settings" else ""
            sql = f"""SELECT count(*), coalesce(md5(string_agg(x::text, '|' ORDER BY x::text)), '') FROM "{t}" x{where}"""  # noqa: S608
            n, h = (await c.execute(text(sql))).one()
            out[t] = (n, h)
    await engine.dispose()
    return out


def _niet_leeg(inhoud: dict[str, tuple[int, str]]) -> None:
    assert len(inhoud) > 10, inhoud  # het hele schema, niet een lege database
    for t in ("users", "homes", "settings", "alembic_version"):
        assert inhoud[t][0] >= 1, t


async def _database_nieuwer(revisie: str):
    """Een database op het huidige schema met gegevens, daarna op een revisie die deze versie niet kent."""
    cm = temp_db("head")
    url = await cm.__aenter__()
    await _vul(url)
    await _zet_revisie(url, revisie)
    return cm, url


def _migrate_op(monkeypatch, url: str, backup_dir) -> None:
    """``cli.main`` roept ``migrate()`` zonder adres aan; hier de echte functie op de proefdatabase."""
    monkeypatch.setattr(cli, "migrate", functools.partial(cli.migrate, url, backup_dir=backup_dir))


@contextlib.asynccontextmanager
async def _app_op(url: str, backup_dir):
    """De hele app op de proefdatabase: ``SHC_DATABASE_URL`` en ``SHC_BACKUP_DIR``, de instellingen opnieuw gelezen en
    de engine/sessionmaker van ``shc_api.db`` opnieuw gemaakt. Zo gebruiken ``cli.main`` (``migrate()`` zonder
    adres), ``cli.current_revision()``, de worker en de API zelf de proefdatabase, en ziet de vergelijking van de
    inhoud elke schrijfactie (R-TECH-1). Daarna alles terug naar de testdatabase van de suite."""
    from shc_api import db
    from shc_api.config import get_settings

    mp = pytest.MonkeyPatch()
    await db.dispose()
    try:
        mp.setenv("SHC_DATABASE_URL", url)
        mp.setenv("SHC_BACKUP_DIR", str(backup_dir))
        get_settings.cache_clear()
        assert get_settings().database_url == url
        yield
    finally:
        await db.dispose()
        mp.undo()
        get_settings.cache_clear()


def _serve(monkeypatch) -> dict:
    import uvicorn

    gezien: dict = {}

    def run(app, **kw):
        gezien["app"] = app
        gezien["kw"] = kw

    monkeypatch.setattr(uvicorn, "run", run)
    cli.main(["serve"])
    return gezien


# --- (a) herkennen vóór de dump --------------------------------------------------------------------------------------


@pytest.mark.parametrize("revisie", NIEUWER)
async def test_nieuwer_schema_geen_dump_geen_migratie_eigen_fout(tmp_path, revisie):
    cm, url = await _database_nieuwer(revisie)
    try:
        voor = await _inhoud(url)
        _niet_leeg(voor)
        assert bk.schema_relation(revisie) == "newer"
        for _ in range(3):  # zoals probe (c): drie starts
            with pytest.raises(cli.SchemaNewerError) as ei:
                await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
            assert isinstance(ei.value, cli.MigrationError)
            assert ei.value.revision == revisie
            assert ei.value.newest_known == bk.head_revision()
            assert str(ei.value).startswith(TEKST)
        with pytest.raises(cli.SchemaNewerError):  # ook zonder dump-stap: nooit migreren
            await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path, pre_dump=False)
        assert list(tmp_path.iterdir()) == []  # geen dump, ook geen half bestand
        assert await _inhoud(url) == voor
        assert cli.current_revision(url) == revisie
    finally:
        await cm.__aexit__(None, None, None)


async def test_shc_api_migrate_weigert_met_eigen_exitcode(monkeypatch, capsys, tmp_path):
    cm, url = await _database_nieuwer("0099_toekomst")
    try:
        voor = await _inhoud(url)
        _migrate_op(monkeypatch, url, tmp_path)
        capsys.readouterr()
        with pytest.raises(SystemExit) as ei:
            cli.main(["migrate"])
        assert ei.value.code == cli.SCHEMA_NIEUWER_EXIT == 3
        err = capsys.readouterr().err
        assert err.startswith("Database-update geweigerd: " + TEKST), err
        assert "0099_toekomst" in err and bk.head_revision() in err
        assert "CommandError" not in err
        assert list(tmp_path.iterdir()) == []
        assert await _inhoud(url) == voor
    finally:
        await cm.__aexit__(None, None, None)


def test_revisie_in_de_melding_zonder_stuurtekens():
    e = cli.SchemaNewerError("0099\n\x1b[31mrood" + "x" * 200, "0004")
    assert "\n" not in e.revision and "\x1b" not in e.revision and len(e.revision) <= 64
    assert e.newest_known == "0004"


# --- (b) herstelmodus: API en worker ---------------------------------------------------------------------------------


def _routes():
    """(methode, pad) voor elke HTTP-route van de gewone app; padparameters ingevuld."""
    from shc_api.main import API_PREFIX, ROUTERS

    for mod in ROUTERS:
        for route in mod.router.routes:
            if isinstance(route, APIRoute):
                pad = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), API_PREFIX + route.path)
                for methode in sorted(route.methods):
                    yield methode, pad


async def test_api_start_in_herstelmodus_en_weigert_alles_behalve_health(monkeypatch, capsys, tmp_path):
    from shc_api.herstel import HerstelApp

    cm, url = await _database_nieuwer("0099_toekomst")
    try:
        voor = await _inhoud(url)
        _niet_leeg(voor)
        async with _app_op(url, tmp_path):  # de app zelf op de proefdatabase: een schrijfactie wordt zichtbaar
            capsys.readouterr()
            gezien = _serve(monkeypatch)  # geen SystemExit: geen herstartlus
            app = gezien["app"]
            assert isinstance(app, HerstelApp)
            assert "db_detail" in gezien["kw"]["log_config"]["filters"]  # zelfde logfilter als de gewone app
            err = capsys.readouterr().err
            assert "Herstelmodus" in err and TEKST in err

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/api/v1/health")
                assert r.status_code == 503
                body = r.json()
                assert body["status"] == "herstelmodus" and body["version"] == VERSION
                assert body["detail"] == {"code": "database_newer", "message": cli.DATABASE_NIEUWER_TEKST}
                assert cli.DATABASE_NIEUWER_TEKST.startswith(TEKST)
                assert r.headers["cache-control"] == "no-store"
                assert r.headers["x-content-type-options"] == "nosniff"

                r = await c.get("/api/v1/system/recovery")  # herstelinfo, zonder aanmelding
                assert r.status_code == 200
                info = r.json()
                assert info["mode"] == "herstelmodus" and info["code"] == "database_newer"
                assert info["message"] == cli.DATABASE_NIEUWER_TEKST
                assert info["database_revision"] == "0099_toekomst"
                assert info["newest_known_revision"] == bk.head_revision()
                assert info["version"] == VERSION

                geteld = {m: 0 for m in ("GET", *UNSAFE)}
                for methode, pad in _routes():
                    if pad == "/api/v1/health":
                        continue
                    r = await c.request(methode, pad, json={} if methode in UNSAFE else None)
                    assert r.status_code == 503, (methode, pad, r.status_code)
                    assert r.json() == {"detail": {"code": "database_newer", "message": cli.DATABASE_NIEUWER_TEKST}}
                    geteld[methode] = geteld.get(methode, 0) + 1
                assert geteld["GET"] >= 20 and sum(geteld[m] for m in UNSAFE) >= 40, geteld  # echt alle routes
                for methode, pad in (
                    ("POST", "/api/v1/auth/login"),
                    ("POST", "/api/v1/auth/setup"),
                    ("GET", "/api/v1/auth/status"),
                    ("POST", "/api/v1/health"),
                    ("GET", "/api/v1/bestaat-niet"),
                    ("GET", "/"),
                ):
                    r = await c.request(methode, pad)
                    assert r.status_code == 503, (methode, pad)
                    assert r.json()["detail"]["code"] == "database_newer"

        assert list(tmp_path.iterdir()) == []  # geen dump
        assert await _inhoud(url) == voor  # niets geschreven (ook geen audit_log, sessie of melding)
    finally:
        await cm.__aexit__(None, None, None)


async def test_herstelmodus_sluit_websocket_en_start_zonder_achtergrondtaken():
    from shc_api.herstel import HerstelApp

    app = HerstelApp("0099_toekomst", bk.head_revision())
    verzonden: list[dict] = []

    async def send(m: dict) -> None:
        verzonden.append(m)

    async def connect() -> dict:
        return {"type": "websocket.connect"}

    await app({"type": "websocket", "path": "/api/v1/ws", "headers": []}, connect, send)
    assert [m["type"] for m in verzonden] == ["websocket.accept", "websocket.close"]  # geen bericht ertussen
    assert verzonden[1]["code"] == 1013 and verzonden[1]["reason"] == "database_newer"

    verzonden.clear()
    berichten = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def lifespan() -> dict:
        return next(berichten)

    await app({"type": "lifespan"}, lifespan, send)
    assert verzonden == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]


async def test_herstelmodus_websocket_client_ziet_1013_met_reden():
    """R-TECH-3: wat een echte client ontvangt. Een echte uvicorn (zoals ``shc-api serve``) met de HerstelApp op een vrije
    poort van 127.0.0.1 en een echte websocket-client: de handshake slaagt en de verbinding sluit met code 1013 en reden
    ``database_newer``. Een sluiting vóór de accept gaf hier HTTP 403 bij de handshake, zonder code en reden."""
    import uvicorn
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    from shc_api.herstel import HerstelApp

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))  # vrije poort van het besturingssysteem (V-008)
    poort = sock.getsockname()[1]
    beleid = asyncio.get_event_loop_policy()
    # loop="asyncio": met "auto" zet uvicorn het uvloop-beleid voor het hele proces, en dan falen latere tests die een
    # subproces starten in de lus van de suite (NotImplementedError). Voor de websocket-handshake maakt de lus niets uit.
    config = uvicorn.Config(HerstelApp("0099_toekomst", bk.head_revision()), log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    draad = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    draad.start()
    try:
        grens = time.monotonic() + 20
        while not server.started and draad.is_alive() and time.monotonic() < grens:
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn startte niet"
        async with connect(f"ws://127.0.0.1:{poort}/api/v1/ws", proxy=None, open_timeout=10) as ws:  # 101: geaccepteerd
            with pytest.raises(ConnectionClosed) as ei:
                await asyncio.wait_for(ws.recv(), timeout=10)  # er komt geen bericht, alleen de sluiting
        assert ei.value.rcvd is not None, ei.value
        assert (ei.value.rcvd.code, ei.value.rcvd.reason) == (1013, "database_newer")
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{poort}", trust_env=False) as c:  # dezelfde server
            r = await c.get("/api/v1/health")
        assert r.status_code == 503 and r.json()["status"] == "herstelmodus"
    finally:
        server.should_exit = True
        await asyncio.to_thread(draad.join, 10)
        sock.close()
    assert not draad.is_alive()
    assert asyncio.get_event_loop_policy() is beleid


@pytest.mark.parametrize("revisie", NIEUWER)
async def test_worker_start_niet_bij_nieuwer_schema_en_wacht(monkeypatch, caplog, tmp_path, revisie):
    from shc_api.worker import core

    cm, url = await _database_nieuwer(revisie)
    try:
        voor = await _inhoud(url)
        _niet_leeg(voor)
        gestart: list[str] = []

        async def run(self) -> None:
            gestart.append("run")

        monkeypatch.setattr(core.Worker, "run", run)
        caplog.set_level(logging.INFO, logger="shc.worker")
        # De worker zelf op de proefdatabase: hij leest de revisie via het echte leespad (cli.current_revision zonder
        # adres) en elke schrijfactie via zijn sessionmaker komt in de vergelijking hieronder (R-TECH-1).
        async with _app_op(url, tmp_path):
            assert cli.current_revision() == revisie
            with pytest.raises(TimeoutError):  # wacht zonder te stoppen: geen exit, dus geen herstartlus
                await asyncio.wait_for(core.run(), timeout=1.5)
        assert gestart == []
        meldingen = [r for r in caplog.records if r.name == "shc.worker" and "nieuwere my_house-versie" in r.getMessage()]
        assert len(meldingen) == 1 and meldingen[0].levelno == logging.ERROR
        assert f"Databaserevisie {revisie};" in meldingen[0].getMessage()
        assert list(tmp_path.iterdir()) == []
        assert await _inhoud(url) == voor  # geen hartslag, geen lus, niets geschreven
    finally:
        await cm.__aexit__(None, None, None)


async def test_worker_wacht_op_een_leesbare_database(monkeypatch, caplog):
    from shc_api.worker import core

    pogingen: list[int] = []

    def lees(_url=None):
        pogingen.append(1)
        if len(pogingen) < 3:
            raise OSError("verbinding geweigerd")
        return bk.head_revision()

    monkeypatch.setattr(cli, "current_revision", lees)
    caplog.set_level(logging.WARNING, logger="shc.worker")
    await asyncio.wait_for(core.wait_while_schema_newer(retry_s=0.01), timeout=10)
    assert len(pogingen) == 3  # nooit blind starten: eerst de revisie lezen, daarna pas verder
    assert len([r for r in caplog.records if "Databaseversie niet leesbaar" in r.getMessage()]) == 1


# --- (c) huidig schema: gewoon starten -------------------------------------------------------------------------------


async def test_huidig_schema_start_gewoon(monkeypatch, tmp_path):
    from shc_api.worker import core

    async with temp_db("head") as url:
        await _vul(url)
        voor = await _inhoud(url)
        # De hele app op de proefdatabase: migrate, serve en de worker lezen de revisie via het echte leespad
        # (cli.current_revision zonder adres), niet via een nagebootste functie.
        async with _app_op(url, tmp_path):
            assert cli.current_revision() == bk.head_revision()
            cli.main(["migrate"])  # geen SystemExit
            gezien = _serve(monkeypatch)
            assert gezien["app"] == "shc_api.main:app"
            assert list(tmp_path.iterdir()) == []  # niets te migreren: geen dump
            assert await _inhoud(url) == voor

            gestart: list[str] = []

            async def run(self) -> None:
                gestart.append("run")

            monkeypatch.setattr(core.Worker, "run", run)
            await asyncio.wait_for(core.run(), timeout=30)
            assert gestart == ["run"]


# --- (d) ouder schema: migreren met dump, zoals voorheen ----------------------------------------------------------------


async def test_ouder_schema_migreert_met_dump(monkeypatch, tmp_path):
    """Een GEVULDE database op schema 0003 (V-003, R-HARDE-EIS-1): na ``shc-api migrate`` is per tabel het aantal rijen
    en de md5 gelijk. Naast alembic_version komen één exact gecontroleerde gate-instelling en drie lege tabellen erbij.
    De dump geeft in een lege database precies de inhoud van vóór de migratie. Een dump met alleen het
    schema (``--schema-only``) valt hier dus op."""
    async with temp_db("0003") as url:
        await _vul(url)
        voor = await _inhoud(url)
        _niet_leeg(voor)
        assert voor["alembic_version"][0] == 1
        backups = tmp_path / "backups"
        _migrate_op(monkeypatch, url, backups)
        started = datetime.now(UTC)
        cli.main(["migrate"])
        finished = datetime.now(UTC)
        assert cli.current_revision(url) == bk.head_revision()
        na = await _inhoud(url, zonder_nieuwe_gate=True)
        verwacht = {t: v for t, v in voor.items() if t != "alembic_version"}
        verwacht.update({t: (0, "") for t in bk.CONNECTOR_LOCAL_TABLES})
        assert {t: v for t, v in na.items() if t != "alembic_version"} == verwacht
        # Uitsluitend deze ene nieuwe rij is toegestaan; bestaande settings blijven integraal vergeleken.
        assert (await _inhoud(url))["settings"][0] == voor["settings"][0] + 1
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text("SELECT value FROM settings WHERE key='commissioning_gate'"))).all()
            assert len(rows) == 1
            value = rows[0][0]
            assert set(value) == {"v"}
            gate = value["v"]
            assert type(gate["version"]) is int and gate["enabled"] is True
            assert gate == {
                "version": 1, "enabled": True, "origin": "fresh",
                "enabled_at": gate["enabled_at"], "enabled_by": "systeem",
            }
            at = datetime.fromisoformat(gate["enabled_at"])
            assert at.utcoffset().total_seconds() == 0
            assert started <= at <= finished
        finally:
            await engine.dispose()
        dumps = sorted(backups.glob("pre-migrate-0003-*.sql.gz"))
        assert len(dumps) == 1, list(backups.iterdir())
        sql = gzip.decompress(dumps[0].read_bytes())
        assert b"bewoner" in sql and b"proef_s_p3_700" in sql  # de ingevoegde waarden staan in de dump

    # Terugzetten zoals in docs/onderhoud.md: de dump in een lege database. Nooit twee dumps letterlijk vergelijken
    # (pg_dump 16.10+ zet een willekeurige \restrict-regel); de vergelijking gaat over de inhoud per tabel.
    psql = shutil.which("psql")
    assert psql, "psql ontbreekt (PATH=/usr/lib/postgresql/16/bin:$PATH)"
    async with temp_db(None) as terug:
        dsn, _pw = cli._libpq(terug)
        r = await asyncio.to_thread(
            subprocess.run, [psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", f"--dbname={dsn}"], input=sql, capture_output=True
        )
        assert r.returncode == 0, r.stderr.decode(errors="replace")[-2000:]
        assert await _inhoud(terug) == voor  # alle tabellen, met dezelfde rijen als vóór de migratie


def test_herstelinfo_is_vaste_json():
    from shc_api.herstel import HerstelApp

    app = HerstelApp("0099_toekomst", "0004")
    status, body = app.answer("GET", "/api/v1/system/recovery")
    assert status == 200
    assert json.loads(json.dumps(body)) == body
    assert set(body) == {"mode", "code", "message", "version", "database_revision", "newest_known_revision"}
