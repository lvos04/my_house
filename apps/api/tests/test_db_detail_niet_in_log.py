"""KLEIN-37 (vervolg op KLEIN-30/KLEIN-31): een onverwachte databasefout mag de DETAIL-regel van PostgreSQL niet in
de log zetten.

PostgreSQL zet bij een schending de sleutelwaarde ("Key (kolom)=(waarde) already exists.") of de hele rij
("Failing row contains (...)") in de DETAIL-regel; hide_parameters (V-094) haalt die niet weg. Zonder maatregel
komt die via de traceback van uvicorn ("Exception in ASGI application") of van de worker-lus in de log. Nu logt de
algemene afhandeling van API en worker alleen soort, sqlstate, constraint en tabel, en een logfilter is het vangnet.
Het HTTP-antwoord blijft wat het was: 500 met de platte tekst "Internal Server Error".
"""

import asyncio
import contextlib
import logging
import uuid

import httpx
import pytest
from sqlalchemy import text

from shc_api.db import engine

from .ws_devices_support import make_worker

TESTWAARDE = "mhklein37testwaarde0123456789"
VERBODEN = (TESTWAARDE, "DETAIL", "Failing row", "Key (")
VERWACHT = {
    "unique": ("23505", "klein37_proef_uniek", "klein37_proef"),
    "check": ("23514", "klein37_proef_kort", "klein37_proef"),
}


async def _schending(soort: str) -> None:
    """Echte schending in een tijdelijke tabel met benoemde constraints, met een herkenbare testwaarde."""
    async with engine().connect() as conn:
        await conn.execute(
            text(
                "CREATE TEMPORARY TABLE klein37_proef (veld text CONSTRAINT klein37_proef_uniek UNIQUE,"
                " CONSTRAINT klein37_proef_kort CHECK (length(veld) < 5))"
            )
        )
        if soort == "unique":
            await conn.execute(text("ALTER TABLE klein37_proef DROP CONSTRAINT klein37_proef_kort"))
            await conn.execute(text("INSERT INTO klein37_proef VALUES (:v)"), {"v": TESTWAARDE})
        await conn.execute(text("INSERT INTO klein37_proef VALUES (:v)"), {"v": TESTWAARDE})


def _controleer(tekst: str, soort: str) -> None:
    for woord in VERBODEN:
        assert woord not in tekst, tekst
    for woord in VERWACHT[soort]:
        assert woord in tekst, tekst


def _uvicorn_zoals(app):
    """Zoals uvicorn: een exceptie die uit de app komt, logt het met traceback op "uvicorn.error"."""

    async def asgi(scope, receive, send):
        try:
            await app(scope, receive, send)
        except Exception:
            logging.getLogger("uvicorn.error").exception("Exception in ASGI application\n")
            raise

    return asgi


@pytest.mark.parametrize("soort", ["unique", "check"])
async def test_nultest_detail_staat_zonder_maatregel_in_de_fout(soort):
    """Zonder maatregel staat de testwaarde in str(fout): de test hieronder kan dus echt falen."""
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError) as ei:
        await _schending(soort)
    assert "DETAIL" in str(ei.value) and TESTWAARDE in str(ei.value)


@pytest.mark.parametrize("soort", ["unique", "check"])
async def test_api_databasefout_zonder_detail_in_log_en_antwoord_ongewijzigd(caplog, soort):
    from shc_api.main import create_app

    app = create_app()

    @app.post("/api/v1/_klein37_proef")
    async def _proef() -> None:
        await _schending(soort)

    caplog.set_level(logging.INFO)
    transport = httpx.ASGITransport(app=_uvicorn_zoals(app), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/v1/_klein37_proef")
    # Antwoord zoals op HEAD gemeten (KLEIN-37): kale 500 van Starlette.
    assert r.status_code == 500
    assert r.text == "Internal Server Error"
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    _controleer(caplog.text, soort)
    assert "POST /api/v1/_klein37_proef" in caplog.text


@pytest.mark.parametrize("soort", ["unique", "check"])
async def test_worker_lus_databasefout_zonder_detail_in_log(caplog, soort):
    w = make_worker()
    pogingen: list[int] = []

    async def work() -> None:
        pogingen.append(1)
        await _schending(soort)

    caplog.set_level(logging.INFO)
    task = asyncio.create_task(w._forever(f"klein37{uuid.uuid4().hex[:6]}", work, 0.01, watch=False))
    try:
        for _ in range(500):
            if pogingen and "faalde" in caplog.text:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert "faalde" in caplog.text
    _controleer(caplog.text, soort)
    assert all(r.exc_info is None for r in caplog.records if "faalde" in r.getMessage())


TRACEBACK_MET_DETAIL = (
    "Traceback (most recent call last):\n"
    '  File "x.py", line 1, in <module>\n'
    'asyncpg.exceptions.UniqueViolationError: duplicate key value violates unique constraint "klein37_proef_uniek"\n'
    f"DETAIL:  Key (veld)=({TESTWAARDE}) already exists.\n"
    "(Background on this error at: https://sqlalche.me/e/20/gkpj)\n"
)


def test_filter_haalt_detail_uit_bericht_en_traceback():
    from shc_api.logfilter import DETAIL_WEGGELATEN, DbDetailFilter

    rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "fout: %s", (TRACEBACK_MET_DETAIL,), None)
    assert DbDetailFilter().filter(rec) is True
    assert TESTWAARDE not in rec.getMessage() and DETAIL_WEGGELATEN in rec.getMessage()
    assert "klein37_proef_uniek" in rec.getMessage()

    rij = logging.LogRecord("x", logging.ERROR, __file__, 1, "Failing row contains (1, %s).", (TESTWAARDE,), None)
    DbDetailFilter().filter(rij)
    assert TESTWAARDE not in rij.getMessage()

    try:
        raise RuntimeError(TRACEBACK_MET_DETAIL)
    except RuntimeError:
        import sys

        exc = logging.LogRecord("x", logging.ERROR, __file__, 1, "Exception in ASGI application", None, sys.exc_info())
    DbDetailFilter().filter(exc)
    tekst = logging.Formatter().format(exc)
    assert TESTWAARDE not in tekst and "DETAIL:" not in tekst and "Exception in ASGI application" in tekst


def test_filter_laat_access_args_staan_en_haakt_eenmaal_aan():
    from shc_api.logfilter import DbDetailFilter, install_log_filter

    rec = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', ("c", "GET", "/", "1.1", 200), None
    )
    DbDetailFilter().filter(rec)
    assert rec.args == ("c", "GET", "/", "1.1", 200)

    logger = logging.getLogger("klein37.proef")
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    try:
        install_log_filter(logger)
        install_log_filter(logger)
        assert sum(isinstance(f, DbDetailFilter) for f in handler.filters) == 1
    finally:
        logger.removeHandler(handler)


def test_uvicorn_logconfig_heeft_filter_op_elke_handler():
    import logging.config

    from shc_api.logfilter import DbDetailFilter, uvicorn_log_config

    config = uvicorn_log_config()
    assert config["handlers"] and all("db_detail" in h["filters"] for h in config["handlers"].values())
    oude = {
        n: (logging.getLogger(n).handlers[:], logging.getLogger(n).propagate)
        for n in ("uvicorn", "uvicorn.error", "uvicorn.access")
    }
    try:
        logging.config.dictConfig({**config, "disable_existing_loggers": False})
        for naam in ("uvicorn", "uvicorn.access"):
            assert all(any(isinstance(f, DbDetailFilter) for f in h.filters) for h in logging.getLogger(naam).handlers)
    finally:
        for n, (handlers, propagate) in oude.items():
            logging.getLogger(n).handlers[:] = handlers
            logging.getLogger(n).propagate = propagate


def test_serve_en_worker_haken_het_filter_aan(monkeypatch):
    import uvicorn

    from shc_api import cli
    from shc_api.logfilter import DbDetailFilter

    root = logging.getLogger()
    handler = logging.StreamHandler()
    root.addHandler(handler)
    gezien: dict = {}
    try:
        monkeypatch.setattr(cli, "migrate", lambda: None)
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: gezien.update(kw))
        cli.main(["serve"])
        assert any(isinstance(f, DbDetailFilter) for f in handler.filters)
        assert "db_detail" in gezien["log_config"]["filters"]

    finally:
        root.removeHandler(handler)


async def test_worker_start_haakt_het_filter_aan(monkeypatch):
    from shc_api.logfilter import DbDetailFilter
    from shc_api.worker import core

    async def niets(self) -> None:
        return None

    monkeypatch.setattr(core.Worker, "run", niets)
    root = logging.getLogger()
    handler = logging.StreamHandler()
    root.addHandler(handler)
    try:
        await core.run()
        assert any(isinstance(f, DbDetailFilter) for f in handler.filters)
    finally:
        root.removeHandler(handler)
