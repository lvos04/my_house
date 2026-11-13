"""V-094: een databasefout mag de waarde van een gebonden parameter niet tonen.

SQLAlchemy zet standaard "[parameters: ...]" in de tekst van een databasefout. Die tekst komt in str(fout),
repr(fout) en via log.exception in de log. Een parameter kan een geheim zijn (wachtwoordhash, token,
sleutel); daarom staan alle productie-engines op hide_parameters=True.
"""

import ast
import logging
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

TESTWAARDE = "mh-v094-testgeheim-0123456789"
SRC = Path(__file__).resolve().parents[1] / "src" / "shc_api"


async def test_databasefout_toont_geen_parameterwaarde(caplog):
    from shc_api.db import engine

    log = logging.getLogger("shc_api")
    with caplog.at_level(logging.ERROR, logger="shc_api"):
        async with engine().connect() as conn:
            await conn.execute(text("CREATE TEMPORARY TABLE v094_proef (veld varchar(8))"))
            with pytest.raises(DBAPIError) as ei:
                await conn.execute(text("INSERT INTO v094_proef (veld) VALUES (:waarde)"), {"waarde": TESTWAARDE})
            await conn.rollback()
        try:
            raise ei.value
        except DBAPIError:
            log.exception("Onverwachte databasefout")

    fout = ei.value
    assert "INSERT INTO v094_proef" in str(fout)  # de SQL-tekst blijft zichtbaar
    assert TESTWAARDE not in str(fout)
    assert TESTWAARDE not in repr(fout)
    assert "Onverwachte databasefout" in caplog.text
    assert TESTWAARDE not in caplog.text


def _engine_aanroepen() -> list[tuple[str, int, ast.Call]]:
    gevonden = []
    for pad in sorted(SRC.rglob("*.py")):
        boom = ast.parse(pad.read_text(encoding="utf-8"))
        for knoop in ast.walk(boom):
            if not isinstance(knoop, ast.Call):
                continue
            naam = getattr(knoop.func, "id", None) or getattr(knoop.func, "attr", None)
            if naam in {"create_async_engine", "create_engine", "engine_from_config", "async_engine_from_config"}:
                gevonden.append((str(pad.relative_to(SRC)), knoop.lineno, knoop))
    return gevonden


def test_alle_productie_engines_verbergen_parameters():
    aanroepen = _engine_aanroepen()
    assert len(aanroepen) >= 12, aanroepen  # db.py, backup.py (3x), cli.py (7x), migrations/env.py
    zonder = [
        f"{pad}:{regel}"
        for pad, regel, knoop in aanroepen
        if not any(
            kw.arg == "hide_parameters" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in knoop.keywords
        )
    ]
    assert zonder == []
