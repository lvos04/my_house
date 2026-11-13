"""KLEIN-39 (vervolg op KLEIN-38): nog vier plekken zetten een fout als tekst in een record, een melding of op stderr.
Is die fout een databasefout, dan mag de DETAIL-regel van PostgreSQL (met een sleutelwaarde of een hele rij) daar
niet in staan; alleen soort, sqlstate, constraint en tabel (via ``fouttekst`` uit shc_api/logfilter.py). Een gewone
fout houdt zijn oude tekst. De plekken:

1. worker/backup_job.py, ``_auto_backup_watch``: "Back-upproces kon niet starten: ..." (back-uprecord en status);
2. worker/climate_loop.py, ``_report_group_error``: de tekst van de melding "Klimaatregeling loopt vast op een zone";
3. worker/commands.py, ``process_commands``: het detail van een mislukt commando;
4. cli.py, ``migrate``: "Database-update mislukt: ..." op stderr.
"""

import uuid

import pytest
from sqlalchemy import select, update

from shc_api import cli, logfilter
from shc_api.db import sessionmaker
from shc_api.models import Alert, CommandRecord
from shc_api.routers import system
from shc_api.worker import backup_job as bj

from .test_api import add_device
from .test_systeem_fouttekst_zonder_detail import _controleer, _echte_fout
from .ws_devices_support import make_worker

SOORTEN = ["unique", "check"]


# --- gedeelde hulpfunctie -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("soort", SOORTEN)
async def test_fouttekst_staat_in_logfilter_en_system_gebruikt_hem(soort):
    fout = await _echte_fout(soort)
    _controleer(logfilter.fouttekst(fout), soort)
    _controleer(logfilter.fouttekst(fout.orig.__cause__), soort)  # de rauwe asyncpg-fout
    assert logfilter.fouttekst(RuntimeError("schijf vol")) == "schijf vol"
    assert system._fouttekst is logfilter.fouttekst  # verplaatst, niet gekopieerd


# --- 1. automatische back-up --------------------------------------------------------------------------------------


async def _auto_backup_start_faalt(monkeypatch, fout: BaseException) -> tuple[dict, list[dict]]:
    async def gooi(*_a, **_k):
        raise fout

    vastgelegd: list[dict] = []

    async def leg_vast(self, result, state, started):
        vastgelegd.append(result)

    monkeypatch.setattr(bj, "run_backup_process", gooi)
    monkeypatch.setattr(type(make_worker()), "record_auto_result", leg_vast)
    resultaat = await make_worker()._auto_backup_watch({})
    return resultaat, vastgelegd


@pytest.mark.parametrize("soort", SOORTEN)
async def test_auto_backup_start_databasefout_zonder_detail(monkeypatch, soort):
    fout = await _echte_fout(soort)
    resultaat, vastgelegd = await _auto_backup_start_faalt(monkeypatch, fout)
    assert resultaat["status"] == "failed" and vastgelegd == [resultaat]
    assert resultaat["error"].startswith("Back-upproces kon niet starten: databasefout "), resultaat
    _controleer(resultaat["error"], soort)


async def test_auto_backup_start_gewone_fout_houdt_oude_tekst(monkeypatch):
    resultaat, _ = await _auto_backup_start_faalt(monkeypatch, RuntimeError("geen proces"))
    assert resultaat == {"status": "failed", "error": "Back-upproces kon niet starten: geen proces"}


# --- 2. klimaatregeling -------------------------------------------------------------------------------------------


async def _klimaatmelding(fout: BaseException) -> str:
    w = make_worker()
    key = f"climate:klein39:{uuid.uuid4().hex}"
    await w._report_group_error(None, key, fout)
    async with sessionmaker()() as db:
        alert = (await db.execute(select(Alert).where(Alert.key == key, Alert.resolved_at.is_(None)))).scalar_one()
        tekst = alert.detail
    await w._report_group_error(None, key, None)  # opruimen: de melding weer opgelost
    return tekst


@pytest.mark.parametrize("soort", SOORTEN)
async def test_klimaatmelding_databasefout_zonder_detail(soort):
    fout = await _echte_fout(soort)
    tekst = await _klimaatmelding(fout)
    assert tekst.startswith("De automatische regeling slaat deze zone over door een fout: databasefout "), tekst
    _controleer(tekst, soort)


async def test_klimaatmelding_gewone_fout_houdt_oude_tekst():
    tekst = await _klimaatmelding(RuntimeError("x" * 400))
    assert tekst == "De automatische regeling slaat deze zone over door een fout: " + "x" * 300


# --- 3. commando's ------------------------------------------------------------------------------------------------


class _KapotteAdapter:
    def __init__(self, fout: BaseException):
        self.fout = fout

    async def execute(self, command):
        raise self.fout


async def _commando_detail(home, fout: BaseException) -> str:
    home_id = uuid.UUID(home["id"])
    dev_id = await add_device(home["id"], "light", f"Klein39 {uuid.uuid4().hex[:6]}")
    async with sessionmaker()() as db:
        cmd = CommandRecord(
            home_id=home_id, device_id=dev_id, action="set_relay", params={"on": True}, source="app", actor="test"
        )
        db.add(cmd)
        await db.commit()
        cmd_id = cmd.id
    w = make_worker()
    w._adapter_for = lambda d: _KapotteAdapter(fout)  # type: ignore[method-assign]
    try:
        await w.process_commands({home_id})
        async with sessionmaker()() as db:
            return (await db.get(CommandRecord, cmd_id)).detail
    finally:  # geen herkansing laten staan voor andere tests
        async with sessionmaker()() as db:
            await db.execute(update(CommandRecord).where(CommandRecord.id == cmd_id).values(status="failed"))
            await db.commit()


@pytest.mark.parametrize("soort", SOORTEN)
async def test_commando_databasefout_zonder_detail(home, soort):
    fout = await _echte_fout(soort)
    detail = await _commando_detail(home, fout)
    assert detail.startswith("databasefout "), detail
    _controleer(detail, soort)


async def test_commando_gewone_fout_houdt_oude_tekst(home):
    assert await _commando_detail(home, RuntimeError("y" * 600)) == "y" * 500
    assert await _commando_detail(home, TimeoutError()) == "TimeoutError"


# --- 4. cli migrate -----------------------------------------------------------------------------------------------


def _migrate_faalt(monkeypatch, capsys, fout: BaseException) -> str:
    def gooi():
        raise fout

    monkeypatch.setattr(cli, "migrate", gooi)
    with pytest.raises(SystemExit) as ei:
        cli.main(["migrate"])
    assert ei.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize("soort", SOORTEN)
async def test_migrate_databasefout_zonder_detail(monkeypatch, capsys, soort):
    fout = await _echte_fout(soort)
    capsys.readouterr()
    err = _migrate_faalt(monkeypatch, capsys, fout)
    assert err.startswith("Database-update mislukt: IntegrityError: databasefout "), err
    _controleer(err, soort)


def test_migrate_gewone_fout_houdt_oude_tekst(monkeypatch, capsys):
    err = _migrate_faalt(monkeypatch, capsys, RuntimeError("schijf vol"))
    assert err == "Database-update mislukt: RuntimeError: schijf vol\n"
