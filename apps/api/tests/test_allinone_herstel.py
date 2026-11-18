"""Gegevensbehoud bij een migratiefout en bij volledig of geredigeerd herstel."""

import asyncio
import gzip
import hashlib
import json
import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from shc_api import backup as bk
from shc_api import cli

from .test_gapfix_followups import PW, _backup
from .test_schema_nieuwer import _inhoud, _vul
from .test_ws_backup_format import temp_db


async def test_tweede_migratie_rolt_hele_reeks_terug(tmp_path):
    async with temp_db("0003") as url:
        await _vul(url)
        engine = create_async_engine(url)
        try:
            async with engine.begin() as c:
                await c.execute(text("""
                    INSERT INTO api_clients (id,user_id,name,token_hash,created_at,role)
                    SELECT gen_random_uuid(),id,'proeftelefoon',gen_random_uuid()::text,now(),'admin'
                    FROM users WHERE username='bewoner'"""))
                await c.execute(text("ALTER TABLE devices ADD COLUMN detection_method varchar(16)"))
            before = await _inhoud(url)
            with pytest.raises(ProgrammingError, match="detection_method"):
                await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
            assert await _inhoud(url) == before
            assert cli.current_revision(url) == "0003"
            async with engine.connect() as c:
                assert (await c.execute(text("SELECT role,expires_at FROM api_clients"))).one() == ("admin", None)
            assert len(list(tmp_path.glob("pre-migrate-0003-*.sql.gz"))) == 1
        finally:
            await engine.dispose()


async def test_dumpcontrole_weigert_beschadiging_en_overschrijven(tmp_path):
    async with temp_db("0003") as url:
        await _vul(url)
        dump = await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
    original = dump.read_bytes()
    checksum = dump.with_name(dump.name + ".sha256")
    assert checksum.read_text().split()[0] == hashlib.sha256(original).hexdigest()
    out = tmp_path / "herstel.sql"
    cli.main(["verify-migration-dump", str(dump), "--out", str(out)])
    assert out.read_bytes() == gzip.decompress(original)
    with pytest.raises(SystemExit) as err:
        cli.main(["verify-migration-dump", str(dump), "--out", str(out)])
    assert err.value.code != 0
    out.unlink()
    for mode in ("afwijkende som", "afgebroken archief", "ontbrekende som"):
        if mode == "afwijkende som":
            dump.write_bytes(original[:-8])
        elif mode == "afgebroken archief":
            checksum.write_text(f"{hashlib.sha256(dump.read_bytes()).hexdigest()}  {dump.name}\n")
        else:
            checksum.unlink()
        with pytest.raises(SystemExit) as err:
            cli.main(["verify-migration-dump", str(dump), "--out", str(out)])
        assert err.value.code != 0
        assert not out.exists()


async def test_geredigeerd_zigbee_bewaart_geen_netwerksleutel_of_kopie(admin, tmp_path):
    z = tmp_path / "z"
    z.mkdir()
    key = list(secrets.token_bytes(16))
    config = "advanced:\n  network_key: " + json.dumps(key) + "\n"
    (z / "configuration.yaml").write_text(config)
    (z / "configuration_backup_v1.yaml").write_text(config)
    (z / "coordinator_backup.json").write_text(json.dumps({"network_key": {"key": bytes(key).hex()}}))
    (z / "andere-kopie").write_bytes(bytes(key))
    red, manifest = await _backup(tmp_path, z, include_secrets=False)
    values = [f.read() for n, f in bk.iter_members(red, PW) if n.startswith("zigbee2mqtt/")]
    assert all(v not in value for value in values for v in (json.dumps(key).encode(), bytes(key).hex().encode(), bytes(key)))
    live = tmp_path / "live"
    live.mkdir()
    old = secrets.token_bytes(45)
    (live / "configuration.yaml").write_bytes(old)
    bk.extract_z2m(red, PW, live, manifest)
    assert (live / "configuration.yaml").read_bytes() == old
    full, manifest = await _backup(tmp_path, z, include_secrets=True)
    assert bk.extract_z2m(full, PW, live, manifest) == 4
    assert all((live / p.name).read_bytes() == p.read_bytes() for p in z.iterdir())
