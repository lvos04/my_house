"""KLEIN-31 (M-1 uit KLEIN-30): een databasefout bij herstel mag de DETAIL-regel van PostgreSQL niet tonen.

PostgreSQL zet bij een schending de sleutelwaarde ("Key (kolom)=(waarde) already exists") of de hele rij
("Failing row contains (...)") in de DETAIL-regel. hide_parameters (V-094) haalt die niet weg. De fouttekst van
een herstel komt in job.error (GET /system/restore/jobs/{id}), in de app-log en in de CLI-uitvoer; daar mogen
alleen sqlstate, constraintnaam en tabel staan.
"""

import argparse
import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from shc_api import backup as bk
from shc_api import cli
from shc_api.routers import system

from .test_ws_backup_format import PW, make_v1, temp_db
from .test_ws_backup_restore import _scalar

TESTWAARDE = "mhklein31testwaarde0123456789"
VERBODEN = (TESTWAARDE, "DETAIL", "Failing row", "Key (")


def _user(uid: str, username: str) -> dict:
    return {
        "id": uid,
        "username": username,
        "password_hash": f"hash-{TESTWAARDE}",
        "role": "admin",
        "disabled": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "failed_logins": 0,
        "locked_until": None,
        "password_changed_at": None,
    }


def _backup(soort: str) -> bytes:
    """Back-up in het schema van nu, met een rij die bij het inladen een schending geeft."""
    uid = str(uuid.uuid4())
    if soort == "unique":  # tweemaal dezelfde gebruikersnaam: DETAIL "Key (username)=(...) already exists."
        tabellen = {"users": [_user(uid, TESTWAARDE), _user(str(uuid.uuid4()), TESTWAARDE)]}
    else:  # rol buiten de CHECK: DETAIL "Failing row contains (...)" met de hele rij, ook code_hash
        tabellen = {
            "users": [_user(uid, "beheerder")],
            "pairing_codes": [
                {
                    "id": str(uuid.uuid4()),
                    "code_hash": TESTWAARDE,
                    "user_id": uid,
                    "label": TESTWAARDE,
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                    "used_at": None,
                    "role": "onbekend",
                }
            ],
        }
    return make_v1(tabellen, bk.head_revision())


VERWACHT = {
    "unique": ("23505", "users_username_key", "users"),
    "check": ("23514", "ck_pairing_codes_role", "pairing_codes"),
}


def _controleer(tekst: str, soort: str) -> None:
    for woord in VERBODEN:
        assert woord not in tekst, tekst
    for woord in VERWACHT[soort]:
        assert woord in tekst, tekst


@pytest.mark.parametrize("soort", ["unique", "check"])
async def test_herstel_fout_toont_sqlstate_en_constraint_maar_geen_detail(tmp_path, soort):
    p = tmp_path / "kapot.shcbak"
    p.write_bytes(_backup(soort))
    async with temp_db() as url:
        with pytest.raises(bk.BackupError) as ei:
            await bk.restore_file(p, PW, url=url)
        assert await _scalar(url, "SELECT count(*) FROM users") == 0  # rollback: niets ingeladen
    tekst = str(ei.value)
    assert tekst.startswith("Herstel mislukt, er is niets gewijzigd: ")
    _controleer(tekst, soort)


async def test_herstel_fout_via_api_en_log_zonder_detail(admin, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0.0)
    echt = bk.restore_file
    async with temp_db() as url:

        async def naar_tijdelijke_db(path, passphrase, **kw):
            return await echt(path, passphrase, url=url, **kw)

        monkeypatch.setattr(bk, "restore_file", naar_tijdelijke_db)
        r = await admin.c.post(
            "/api/v1/system/restore/verify",
            files={"file": ("kapot.shcbak", _backup("check"), "application/octet-stream")},
            data={"passphrase": PW},
            headers=admin.h(),
        )
        assert r.status_code == 200, r.text
        with caplog.at_level(logging.WARNING, logger="shc_api"):
            upload_id = r.json()["upload_id"]
            r = await admin.post("/system/restore/apply", {"upload_id": upload_id, "passphrase": PW})
            assert r.status_code == 202, r.text
            job_id = r.json()["job_id"]
            for _ in range(600):
                resp = await admin.c.get(f"/api/v1/system/restore/jobs/{job_id}")
                job = resp.json()
                if job["state"] != "running":
                    break
                await asyncio.sleep(0.1)
    assert job["state"] == "failed", job
    _controleer(job["error"], "check")
    _controleer(resp.text, "check")
    assert "Herstel mislukt" in caplog.text
    _controleer(caplog.text, "check")
    weg = await admin.c.delete(f"/api/v1/system/restore/uploads/{upload_id}", headers=admin.h())
    assert weg.status_code == 204  # een mislukt herstel laat de upload staan


async def test_herstel_fout_via_cli_zonder_detail(admin, tmp_path, monkeypatch, capsys):
    p = tmp_path / "kapot.shcbak"
    p.write_bytes(_backup("unique"))
    monkeypatch.setenv("SHC_BACKUP_PASSPHRASE", PW)
    args = argparse.Namespace(
        file=str(p), passphrase_file=None, passphrase_stdin=False, z2m_dir=None, yes=True, mode="same_home"
    )

    async def geen_worker(url=None):
        return (False, False)

    monkeypatch.setattr(cli, "worker_running", geen_worker)
    echt = bk.restore_file
    async with temp_db() as url:

        async def naar_tijdelijke_db(path, passphrase, **kw):
            return await echt(path, passphrase, url=url, **kw)

        monkeypatch.setattr(bk, "restore_file", naar_tijdelijke_db)
        try:
            assert await cli._restore(args, dry_run=False) == 2
        finally:
            await cli.set_maintenance(False)
    uit = capsys.readouterr()
    assert "MISLUKT (er is niets gewijzigd)" in uit.err
    _controleer(uit.out + uit.err, "unique")


class _NepFout(Exception):
    """Lijkt op de fout van de driver: tekst met DETAIL, losse velden zonder."""

    sqlstate = "23505"
    constraint_name = "nep_constraint"
    table_name = "nep_tabel"

    def __str__(self) -> str:
        return f"duplicate key\nDETAIL:  Key (token_hash)=({TESTWAARDE}) already exists."


@pytest.mark.parametrize("wat", ["backup", "dump"])
async def test_backup_en_dump_fout_zonder_detail(admin, tmp_path, monkeypatch, wat):
    from sqlalchemy.exc import IntegrityError

    def gooi(*_a, **_k):
        raise IntegrityError("INSERT ...", None, _NepFout())

    async def gooi_async(*a, **k):
        gooi()

    if wat == "backup":
        monkeypatch.setattr(bk, "write_backup", gooi_async)
        with pytest.raises(bk.BackupError) as ei:
            await bk.run_backup(PW, True, backup_dir=tmp_path)
        begin = "Back-up mislukt: "
    else:
        monkeypatch.setattr(bk, "_pg_dump_sync", gooi)
        with pytest.raises(bk.BackupError) as ei:
            await bk.run_db_dump(PW, backup_dir=tmp_path)
        begin = "Database-dump mislukt: "
    tekst = str(ei.value)
    assert tekst.startswith(begin), tekst
    for woord in VERBODEN:
        assert woord not in tekst, tekst
    for woord in ("23505", "nep_constraint", "nep_tabel"):
        assert woord in tekst, tekst
    bewaard = await admin.c.get("/api/v1/system/backups", headers=admin.h())
    assert TESTWAARDE not in bewaard.text and "DETAIL" not in bewaard.text


async def test_andere_fout_dan_databasefout_blijft_zoals_hij_is(admin, tmp_path, monkeypatch):
    async def gooi(*_a, **_k):
        raise RuntimeError("schijf vol")

    monkeypatch.setattr(bk, "write_backup", gooi)
    with pytest.raises(bk.BackupError, match="^Back-up mislukt: RuntimeError: schijf vol$"):
        await bk.run_backup(PW, True, backup_dir=tmp_path)
