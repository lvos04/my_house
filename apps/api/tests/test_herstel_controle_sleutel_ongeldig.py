"""KLEIN-41 (vervolg op KLEIN-40): POST /system/restore/verify roept de sleutelcontrole (decrypt_check)
aan. Een ontbrekende SHC_SECRET_KEY (RuntimeError) of een te korte (ValueError uit SecretBox) viel door
tot een kale 500 "Internal Server Error", en het geüploade bestand bleef staan. Nu weigert de route met
503 ``secret_key_invalid`` en een vaste Nederlandse tekst zonder (een deel van) de sleutel, en ruimt de
upload op.
"""

import logging

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api.config import get_settings

PW = "sleutel-wachtwoordzin-123"
TE_KORT = "KortGeheimQ7zX-9wv"  # 18 tekens: SecretBox weigert hem


def _geen_sleutelmateriaal(tekst: str) -> None:
    assert TE_KORT not in tekst
    for i in range(len(TE_KORT) - 5):  # ook geen stuk van 6 tekens
        assert TE_KORT[i : i + 6] not in tekst


def _uploads() -> set[str]:
    d = get_settings().backup_dir / "uploads"
    return {p.name for p in d.glob("*")} if d.exists() else set()


@pytest.mark.parametrize("sleutel", [TE_KORT, None], ids=["te_kort", "ontbreekt"])
async def test_controle_weigert_zonder_sleutelmateriaal_en_ruimt_upload_op(
    app, admin, tmp_path, monkeypatch, caplog, sleutel
):
    target = tmp_path / "b.shcbak"
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            await bk.write_backup(c, target, PW, True)
    finally:
        await engine.dispose()
    voor = _uploads()
    monkeypatch.setattr(get_settings(), "secret_key_file", None)
    monkeypatch.setattr(get_settings(), "secret_key", sleutel)
    caplog.set_level(logging.DEBUG)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=admin.c.cookies) as c:
        r = await c.post(
            "/api/v1/system/restore/verify",
            files={"file": ("b.shcbak", target.read_bytes(), "application/octet-stream")},
            data={"passphrase": PW},
            headers=admin.h(),
        )
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "secret_key_invalid"
    assert detail["message"] == "De sleutel van deze server ontbreekt of is ongeldig; deze actie is niet mogelijk."
    assert _uploads() <= voor  # het geüploade bestand is weer weg
    _geen_sleutelmateriaal(r.text + caplog.text)


async def test_goede_sleutel_controleert_gewoon(admin, tmp_path):
    target = tmp_path / "b.shcbak"
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            await bk.write_backup(c, target, PW, True)
    finally:
        await engine.dispose()
    r = await admin.c.post(
        "/api/v1/system/restore/verify",
        files={"file": ("b.shcbak", target.read_bytes(), "application/octet-stream")},
        data={"passphrase": PW},
        headers=admin.h(),
    )
    assert r.status_code == 200, r.text
    assert (await admin.delete(f"/system/restore/uploads/{r.json()['upload_id']}")).status_code == 204
