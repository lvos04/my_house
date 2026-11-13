"""KLEIN-38 (vervolg op KLEIN-31/KLEIN-37): routers/system.py zet een fout als tekst in een HTTP-antwoord, in de
job van een herstel en in de log. Is die fout een databasefout, dan mag de DETAIL-regel van PostgreSQL (met een
sleutelwaarde of een hele rij) daar niet in staan; alleen soort, sqlstate, constraint en tabel. Twee plekken:
de start van een handmatige back-up ("Back-upproces kon niet starten: ...") en een herstel-job die faalt met een
fout die geen BackupError is. Een gewone fout houdt zijn oude tekst.
"""

import asyncio
import logging

import pytest
from sqlalchemy.exc import DBAPIError

from shc_api import backup as bk
from shc_api.routers import system

from .test_db_detail_niet_in_log import VERWACHT, _schending
from .test_herstel_databasefout_zonder_detail import _backup
from .test_ws_backup_format import PW

TESTWAARDE = "mhklein37testwaarde0123456789"  # de waarde die _schending gebruikt
VERBODEN = (TESTWAARDE, "DETAIL", "Failing row", "Key (")


async def _echte_fout(soort: str) -> DBAPIError:
    """Een echte IntegrityError van PostgreSQL (asyncpg), met de testwaarde in de DETAIL-regel."""
    with pytest.raises(DBAPIError) as ei:
        await _schending(soort)
    assert "DETAIL" in str(ei.value) and TESTWAARDE in str(ei.value)  # zonder maatregel lekt de waarde
    return ei.value


def _controleer(tekst: str, soort: str) -> None:
    for woord in VERBODEN:
        assert woord not in tekst, tekst
    for woord in VERWACHT[soort]:
        assert woord in tekst, tekst


async def _backup_start_faalt(admin, monkeypatch, fout: BaseException):
    async def gooi(*_a, **_k):
        raise fout

    monkeypatch.setattr(system, "run_backup_process", gooi)
    r = await admin.post("/system/backups", {"passphrase": PW, "include_secrets": True})
    lijst = await admin.c.get("/api/v1/system/backups", headers=admin.h())
    return r, lijst


async def _herstel_faalt(admin, monkeypatch, fout: BaseException):
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0.0)

    async def gooi(*_a, **_k):
        raise fout

    monkeypatch.setattr(bk, "restore_file", gooi)
    r = await admin.c.post(
        "/api/v1/system/restore/verify",
        files={"file": ("proef.shcbak", _backup("unique"), "application/octet-stream")},
        data={"passphrase": PW},
        headers=admin.h(),
    )
    assert r.status_code == 200, r.text
    upload_id = r.json()["upload_id"]
    r = await admin.post("/system/restore/apply", {"upload_id": upload_id, "passphrase": PW})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    for _ in range(600):
        resp = await admin.c.get(f"/api/v1/system/restore/jobs/{job_id}")
        if resp.json()["state"] != "running":
            break
        await asyncio.sleep(0.05)
    weg = await admin.c.delete(f"/api/v1/system/restore/uploads/{upload_id}", headers=admin.h())
    assert weg.status_code == 204  # een mislukt herstel laat de upload staan
    return resp


@pytest.mark.parametrize("soort", ["unique", "check"])
async def test_backup_start_databasefout_zonder_detail(admin, monkeypatch, caplog, soort):
    fout = await _echte_fout(soort)
    with caplog.at_level(logging.DEBUG):
        r, lijst = await _backup_start_faalt(admin, monkeypatch, fout)
    assert r.status_code == 500, r.text
    assert r.json()["detail"]["code"] == "backup_failed", r.text
    assert "Back-upproces kon niet starten: databasefout " in r.text, r.text
    _controleer(r.text, soort)
    _controleer(lijst.text, soort)
    for woord in VERBODEN:
        assert woord not in caplog.text, caplog.text


async def test_backup_start_gewone_fout_houdt_oude_tekst(admin, monkeypatch):
    r, _ = await _backup_start_faalt(admin, monkeypatch, RuntimeError("geen proces"))
    assert r.status_code == 500, r.text
    assert "Back-upproces kon niet starten: geen proces" in r.text, r.text


@pytest.mark.parametrize("soort", ["unique", "check"])
async def test_herstel_job_databasefout_zonder_detail(admin, monkeypatch, caplog, soort):
    fout = await _echte_fout(soort)
    with caplog.at_level(logging.DEBUG):
        resp = await _herstel_faalt(admin, monkeypatch, fout)
    job = resp.json()
    assert job["state"] == "failed", job
    assert job["error"].startswith("IntegrityError: databasefout "), job
    _controleer(job["error"], soort)
    _controleer(resp.text, soort)
    assert "Herstel mislukt" in caplog.text
    _controleer(caplog.text, soort)


async def test_herstel_job_gewone_fout_houdt_oude_tekst(admin, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="shc_api"):
        resp = await _herstel_faalt(admin, monkeypatch, RuntimeError("schijf vol"))
    job = resp.json()
    assert job["state"] == "failed", job
    assert job["error"] == "RuntimeError: schijf vol", job
    assert "Herstel mislukt: RuntimeError: schijf vol" in caplog.text


@pytest.mark.parametrize("soort", ["unique", "check"])
async def test_fouttekst_ook_voor_rauwe_driverfout(soort):
    import asyncpg

    fout = await _echte_fout(soort)
    rauw = fout.orig.__cause__  # de asyncpg-fout zelf, zonder omhulsel van SQLAlchemy
    assert isinstance(rauw, asyncpg.PostgresError)
    _controleer(system._fouttekst(rauw), soort)
    assert system._fouttekst(RuntimeError("schijf vol")) == "schijf vol"
