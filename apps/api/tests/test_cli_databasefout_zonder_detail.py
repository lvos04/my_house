"""KLEIN-53 (A-05 uit KLEIN-48, vervolg op KLEIN-37/KLEIN-39): de cli-commando's buiten serve/migrate/worker
(backup, restore, verify-backup, restore-drill, rekey, reset-password, maintenance, decrypt-dump).

Gemeten op HEAD: een databasefout die uit zo'n handler komt, zette Python met de volledige traceback op stderr, met
daarin de DETAIL-regel van PostgreSQL (sleutelwaarde of hele rij); een waarschuwing via ``log.warning`` (zoals
"Status niet doorgegeven aan open schermen") ging via ``logging.lastResort`` zonder logfilter naar stderr, ook met
DETAIL. Nu:

1. een databasefout uit de handler geeft één regel "Opdracht <naam> mislukt: <soort>: <fouttekst>" op stderr
   (alleen soort, sqlstate, constraint en tabel) en exitcode 1, zoals voorheen bij een onafgevangen fout;
2. het logfilter hangt ook aan ``logging.lastResort``, de handler die deze commando's gebruiken.

Een gewone fout gaat ongewijzigd door.
"""

import asyncio
import logging
import os
import subprocess
import sys
import textwrap

import pytest

from shc_api import cli
from shc_api.logfilter import DETAIL_WEGGELATEN, DbDetailFilter

from .test_systeem_fouttekst_zonder_detail import TESTWAARDE, VERBODEN, _controleer, _echte_fout

SOORTEN = ["unique", "check"]

# commando -> (functie in cli.py die de handler is, extra argumenten voor de parser)
COMMANDO = {
    "backup": ("_backup", []),
    "restore": ("_restore", ["proef.shc"]),
    "verify-backup": ("_restore", ["proef.shc"]),
    "restore-drill": ("_restore_drill", ["proef.shc"]),
    "rekey": ("_rekey", ["--old-key-file", "proef.key"]),
    "reset-password": ("_reset_password", ["iemand"]),
    "maintenance": ("_maintenance", ["status"]),
    "decrypt-dump": ("_decrypt_dump", ["proef.shcdump", "--out", "proef.dump"]),
}


@pytest.fixture
def eigen_last_resort(monkeypatch):
    """Een eigen lastResort-handler per test, zodat het aanhaken van het filter niet buiten de test blijft."""
    handler = logging._StderrHandler(logging.WARNING)
    monkeypatch.setattr(logging, "lastResort", handler)
    return handler


def _handler_faalt(monkeypatch, cmd: str, fout: BaseException) -> None:
    async def gooi(*_a, **_k):
        raise fout

    monkeypatch.setattr(cli, COMMANDO[cmd][0], gooi)
    cli.main([cmd, *COMMANDO[cmd][1]])


async def _handler_faalt_in_thread(monkeypatch, cmd: str, fout: BaseException) -> None:
    """cli.main doet zelf asyncio.run; vanuit een async test dus in een eigen thread."""
    await asyncio.to_thread(_handler_faalt, monkeypatch, cmd, fout)


# --- 1. databasefout uit de handler ---------------------------------------------------------------------------------


@pytest.mark.parametrize("soort", SOORTEN)
@pytest.mark.parametrize("cmd", sorted(COMMANDO))
async def test_handler_databasefout_zonder_detail(monkeypatch, capsys, eigen_last_resort, cmd, soort):
    fout = await _echte_fout(soort)
    capsys.readouterr()
    with pytest.raises(SystemExit) as ei:
        await _handler_faalt_in_thread(monkeypatch, cmd, fout)
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith(f"Opdracht {cmd} mislukt: IntegrityError: databasefout "), err
    assert err.count("\n") == 1, err
    _controleer(err, soort)


async def test_handler_rauwe_driverfout_zonder_detail(monkeypatch, capsys, eigen_last_resort):
    fout = (await _echte_fout("unique")).orig.__cause__  # de asyncpg-fout zelf
    capsys.readouterr()
    with pytest.raises(SystemExit) as ei:
        await _handler_faalt_in_thread(monkeypatch, "maintenance", fout)
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("Opdracht maintenance mislukt: UniqueViolationError: databasefout "), err
    _controleer(err, "unique")


def test_handler_gewone_fout_gaat_ongewijzigd_door(monkeypatch, eigen_last_resort):
    fout = RuntimeError("schijf vol")
    with pytest.raises(RuntimeError) as ei:
        _handler_faalt(monkeypatch, "maintenance", fout)
    assert ei.value is fout


def test_handler_exitcode_ongewijzigd(monkeypatch, eigen_last_resort):
    async def code(_args):
        return 3

    monkeypatch.setattr(cli, "_maintenance", code)
    with pytest.raises(SystemExit) as ei:
        cli.main(["maintenance", "status"])
    assert ei.value.code == 3


# --- 2. logfilter op lastResort -------------------------------------------------------------------------------------


def test_cli_haakt_filter_aan_op_last_resort_eenmaal(monkeypatch, eigen_last_resort):
    async def niets(_args):
        return 0

    monkeypatch.setattr(cli, "_maintenance", niets)
    cli.main(["maintenance", "status"])
    cli.main(["maintenance", "status"])
    assert sum(isinstance(f, DbDetailFilter) for f in eigen_last_resort.filters) == 1


# --- 3. echt proces: stderr zoals de gebruiker hem ziet ---------------------------------------------------------------

_SCHENDING = """
import asyncio, sys
from sqlalchemy import text
from shc_api import cli
from shc_api.db import engine

WAARDE = "mhklein37testwaarde0123456789"

async def schending(*_a, **_k):
    async with engine().connect() as conn:
        await conn.execute(text(
            "CREATE TEMPORARY TABLE klein53_proef (veld text CONSTRAINT klein53_proef_uniek UNIQUE)"))
        await conn.execute(text("INSERT INTO klein53_proef VALUES (:v)"), {"v": WAARDE})
        await conn.execute(text("INSERT INTO klein53_proef VALUES (:v)"), {"v": WAARDE})
"""


def _proces(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_SCHENDING) + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=120,
        check=False,
    )


def test_proces_handler_databasefout_een_regel_zonder_detail():
    p = _proces(
        """
        cli._maintenance = schending
        cli.main(["maintenance", "status"])
        """
    )
    assert p.returncode == 1, p.stderr
    assert p.stderr.startswith("Opdracht maintenance mislukt: IntegrityError: databasefout "), p.stderr
    assert p.stderr.count("\n") == 1, p.stderr  # geen traceback
    for woord in VERBODEN:
        assert woord not in p.stderr, p.stderr
    for woord in ("23505", "klein53_proef_uniek", "klein53_proef"):
        assert woord in p.stderr, p.stderr


def test_proces_waarschuwing_via_last_resort_zonder_detail():
    p = _proces(
        """
        import shc_api.views.status as status

        async def handler(_args):
            await cli.publish_status_now()
            return 0

        status.publish_status = schending
        cli._maintenance = handler
        cli.main(["maintenance", "status"])
        """
    )
    assert p.returncode == 0, p.stderr
    assert p.stderr.startswith("Status niet doorgegeven aan open schermen: "), p.stderr
    assert "klein53_proef_uniek" in p.stderr, p.stderr
    assert DETAIL_WEGGELATEN in p.stderr, p.stderr  # de aantekening van het filter op de plek van de DETAIL-regel
    for woord in (TESTWAARDE, "DETAIL:", "Failing row", "Key ("):
        assert woord not in p.stderr, p.stderr
