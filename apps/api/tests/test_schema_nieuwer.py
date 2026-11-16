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
(d) een database op een ouder schema migreert zoals voorheen, met dump.

De tabellen worden vergeleken met inhoud (niet leeg, V-003); de herkenning gebruikt de migratieketen, niet de letterlijke
waarde "0099" (V-086: ook "0005", het eerstvolgende nummer, telt als nieuwer).
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import uuid

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
NIEUWER = ["0099_toekomst", "0005"]
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


async def _inhoud(url: str) -> dict[str, tuple[int, str]]:
    """Per tabel (ook alembic_version): aantal rijen en een md5 over alle rijen."""
    engine = create_async_engine(url, poolclass=NullPool)
    out: dict[str, tuple[int, str]] = {}
    async with engine.connect() as c:
        tabellen = (
            await c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"))
        ).scalars()
        for t in list(tabellen):
            # tabelnamen uit pg_tables van de eigen proefdatabase
            sql = f"""SELECT count(*), coalesce(md5(string_agg(x::text, '|' ORDER BY x::text)), '') FROM "{t}" x"""  # noqa: S608
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
        _migrate_op(monkeypatch, url, tmp_path)
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
    assert len(verzonden) == 1
    assert verzonden[0]["type"] == "websocket.close" and verzonden[0]["code"] == 1013

    verzonden.clear()
    berichten = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def lifespan() -> dict:
        return next(berichten)

    await app({"type": "lifespan"}, lifespan, send)
    assert verzonden == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]


async def test_worker_start_niet_bij_nieuwer_schema_en_wacht(monkeypatch, caplog):
    from shc_api.worker import core

    cm, url = await _database_nieuwer("0099_toekomst")
    try:
        voor = await _inhoud(url)
        _niet_leeg(voor)
        echte = cli.current_revision
        monkeypatch.setattr(cli, "current_revision", lambda _url=None: echte(url))
        gestart: list[str] = []

        async def run(self) -> None:
            gestart.append("run")

        monkeypatch.setattr(core.Worker, "run", run)
        caplog.set_level(logging.INFO, logger="shc.worker")
        with pytest.raises(TimeoutError):  # wacht zonder te stoppen: geen exit, dus geen herstartlus
            await asyncio.wait_for(core.run(), timeout=1.5)
        assert gestart == []
        meldingen = [r for r in caplog.records if r.name == "shc.worker" and "nieuwere my_house-versie" in r.getMessage()]
        assert len(meldingen) == 1 and meldingen[0].levelno == logging.ERROR
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
        _migrate_op(monkeypatch, url, tmp_path)
        cli.main(["migrate"])  # geen SystemExit
        gezien = _serve(monkeypatch)
        assert gezien["app"] == "shc_api.main:app"
        assert list(tmp_path.iterdir()) == []  # niets te migreren: geen dump
        assert await _inhoud(url) == voor

        echte = cli.current_revision
        monkeypatch.setattr(cli, "current_revision", lambda _url=None: echte(url))
        gestart: list[str] = []

        async def run(self) -> None:
            gestart.append("run")

        monkeypatch.setattr(core.Worker, "run", run)
        await asyncio.wait_for(core.run(), timeout=30)
        assert gestart == ["run"]


# --- (d) ouder schema: migreren met dump, zoals voorheen ----------------------------------------------------------------


async def test_ouder_schema_migreert_met_dump(monkeypatch, tmp_path):
    import gzip

    async with temp_db("0003") as url:
        _migrate_op(monkeypatch, url, tmp_path)
        cli.main(["migrate"])
        dumps = sorted(tmp_path.glob("pre-migrate-0003-*.sql.gz"))
        assert len(dumps) == 1, list(tmp_path.iterdir())
        assert b"CREATE TABLE" in gzip.decompress(dumps[0].read_bytes())
        assert cli.current_revision(url) == bk.head_revision()


def test_herstelinfo_is_vaste_json():
    from shc_api.herstel import HerstelApp

    app = HerstelApp("0099_toekomst", "0004")
    status, body = app.answer("GET", "/api/v1/system/recovery")
    assert status == 200
    assert json.loads(json.dumps(body)) == body
    assert set(body) == {"mode", "code", "message", "version", "database_revision", "newest_known_revision"}
