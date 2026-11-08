"""Backup stream: chunked AEAD file format, v1 compatibility, streaming with flat memory,
redaction and Zigbee2MQTT reporting (verbeterlijst 5, 33, kleine punten)."""

import asyncio
import contextlib
import hashlib
import io
import json
import os
import sys
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api.config import get_settings

PW = "een lange wachtwoordzin"


# --- helpers shared by the backup test files -----------------------------------------------------


def _admin_dsn() -> str:
    u = urlsplit(get_settings().database_url.replace("+asyncpg", ""))
    return urlunsplit((u.scheme, u.netloc, "/postgres", "", ""))


@contextlib.asynccontextmanager
async def temp_db(revision: str | None = "head"):
    """A fresh database (migrated to `revision`, or empty with None); dropped afterwards."""
    from alembic import command

    name = f"shc_tmp_{uuid.uuid4().hex[:10]}"
    conn = await asyncpg.connect(_admin_dsn())
    await conn.execute(f'CREATE DATABASE "{name}"')
    await conn.close()
    u = urlsplit(get_settings().database_url)
    url = urlunsplit((u.scheme, u.netloc, f"/{name}", "", ""))
    try:
        if revision:
            await asyncio.to_thread(command.upgrade, bk.alembic_config(url), revision)
        yield url
    finally:
        conn = await asyncpg.connect(_admin_dsn())
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.close()


def make_v1(tables: dict[str, list[dict]], schema_revision: str, passphrase: str = PW) -> bytes:
    """A backup file in the old v1 format (one AES-GCM blob, manifest first, one file per table)."""
    files = {}
    info = {}
    for t, rows in tables.items():
        data = "".join(json.dumps(r) + "\n" for r in rows).encode()
        files[f"tables/{t}.jsonl"] = data
        info[t] = {"rows": len(rows), "file": f"tables/{t}.jsonl"}
    created = datetime.now(UTC).isoformat()
    manifest = {
        "format": "shc-backup",
        "format_version": 1,
        "app_version": "0.1.0",
        "schema_revision": schema_revision,
        "created_at": created,
        "secrets_included": True,
        "zigbee2mqtt_included": False,
        "tables": info,
        "checksums": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in [("manifest.json", json.dumps(manifest).encode()), *files.items()]:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    return bk.encrypt(buf.getvalue(), passphrase, created)


def write_chunked(path: Path, data: bytes, chunk_size: int = 4096, passphrase: str = PW) -> None:
    with open(path, "wb") as raw:
        enc = bk.EncryptingWriter(raw, passphrase, "2026-11-08T00:00:00Z", chunk_size=chunk_size)
        with tarfile.open(fileobj=enc, mode="w|gz") as tar:
            ti = tarfile.TarInfo("data.bin")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
        enc.finish()


def read_member(path: Path, passphrase: str = PW) -> bytes:
    return b"".join(f.read() for _n, f in bk.iter_members(path, passphrase))


# --- format ------------------------------------------------------------------------------------


def test_chunked_format_roundtrip_and_every_kind_of_tampering_is_detected(tmp_path):
    data = os.urandom(60_000)  # incompressible: many 4 KiB chunks
    p = tmp_path / "a.shcbak"
    write_chunked(p, data)
    raw = p.read_bytes()
    assert raw.startswith(b"SHCBAK2\n")
    assert read_member(p) == data
    header_end = raw.index(b"\n", 8) + 1
    body = raw[header_end:]
    csz = 4096 + 16
    assert len(body) > 3 * csz

    def bad(blob: bytes, msg: str | None = None):
        q = tmp_path / f"{uuid.uuid4().hex}.shcbak"
        q.write_bytes(blob)
        with pytest.raises(bk.BackupError) as ei:
            read_member(q)
        if msg:
            assert msg in str(ei.value)

    flipped = bytearray(raw)
    flipped[header_end + csz + 10] ^= 1
    bad(bytes(flipped), "beschadigd")
    bad(raw[: -csz // 2])  # truncated inside the last chunk
    bad(raw[: header_end + 2 * csz])  # truncated exactly at a chunk boundary
    bad(raw[:header_end] + body[:csz] + body[2 * csz :])  # a chunk dropped
    bad(raw[:header_end] + body[csz : 2 * csz] + body[:csz] + body[2 * csz :])  # chunks swapped
    bad(raw + b"extra")  # data appended
    bad(raw.replace(b'"created_at":"2026', b'"created_at":"2025', 1))  # header edited (it is AAD)
    with pytest.raises(bk.BackupError, match="verkeerde wachtwoordzin"):
        read_member(p, "een andere wachtwoordzin")


def test_v1_files_are_still_readable(tmp_path):
    p = tmp_path / "oud.shcbak"
    p.write_bytes(make_v1({"homes": [{"id": str(uuid.uuid4()), "name": "Oud huis"}]}, "0001"))
    v = bk.verify_file(p, PW)
    assert v.format_version == 1
    assert v.manifest["tables"]["homes"] == {"rows": 1, "files": ["tables/homes.jsonl"]}
    assert v.summary["homes"][0]["name"] == "Oud huis"
    bad = bytearray(p.read_bytes())
    bad[-5] ^= 1
    p.write_bytes(bytes(bad))
    with pytest.raises(bk.BackupError):
        bk.verify_file(p, PW)


# --- streaming ------------------------------------------------------------------------------------


CHILD = """
import asyncio, resource, sys
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from shc_api import backup as bk

async def main():
    e = create_async_engine(sys.argv[1], poolclass=NullPool)
    async with e.connect() as c:
        m = await bk.write_backup(c, Path(sys.argv[2]), sys.argv[3], True)
    await e.dispose()
    return m["tables"]["sensor_readings"]

sr = asyncio.run(main())
print(sr["rows"], len(sr["files"]), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
"""


async def _backup_in_child(url: str, path: Path) -> tuple[int, int, int]:
    """Run write_backup in a fresh process; (rows, parts, peak RSS in KiB)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", CHILD, url, str(path), PW, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()[-2000:]
    rows, parts, rss = (int(x) for x in out.decode().split())
    return rows, parts, rss


async def test_backup_of_200k_readings_streams_with_flat_memory(tmp_path):
    async with temp_db() as url:
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as c:
            hid, did = uuid.uuid4(), uuid.uuid4()
            await c.execute(
                text(
                    "INSERT INTO homes (id, name, timezone, currency, active_from) VALUES (:h, 'T', 'UTC', 'EUR', now())"
                ),
                {"h": hid},
            )
            await c.execute(
                text(
                    "INSERT INTO devices (id, home_id, provider, kind, name, stable_identity, capabilities, compatibility,"
                    " lifecycle, online, state, discovered_at) VALUES (:d, :h, 'test', 'p1_meter', 'P1', 'x', '[]',"
                    " 'untested', 'active', true, '{}', now())"
                ),
                {"d": did, "h": hid},
            )
        base_rows, _, base_rss = await _backup_in_child(url, tmp_path / "leeg.shcbak")
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO sensor_readings (device_id, metric, value, unit, quality, observed_at, received_at,"
                    " event_id) SELECT :d, 'power_w', g::float, 'W', 'measured', now() - make_interval(secs => g),"
                    " now(), 'e' || g FROM generate_series(1, 200000) g"
                ),
                {"d": did},
            )
        await engine.dispose()
        path = tmp_path / "groot.shcbak"
        rows, parts, rss = await _backup_in_child(url, path)
    assert base_rows == 0 and rows == 200_000
    assert parts == 200_000 // bk.PART_ROWS  # written part by part, never as one blob
    growth_mb = (rss - base_rss) / 1024
    assert growth_mb < 40, f"geheugen groeide {growth_mb:.0f} MB voor 200.000 metingen"
    v = await asyncio.to_thread(bk.verify_file, path, PW, False)
    assert v.manifest["tables"]["sensor_readings"]["rows"] == 200_000
    assert v.manifest["schema_revision"] == bk.head_revision() and v.manifest["app_version"]


# --- redaction and zigbee2mqtt ---------------------------------------------------------------------


async def test_redacted_backup_contains_no_password_or_token_hashes(admin, tmp_path):
    from shc_api.db import sessionmaker
    from shc_api.store import set_secret_setting

    async with sessionmaker()() as db:
        await set_secret_setting(db, "backup_passphrase_enc", "geheime wachtwoordzin 1")
        await db.commit()
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            await bk.write_backup(c, tmp_path / "r.shcbak", PW, include_secrets=False)
    finally:
        await engine.dispose()
    rows: dict[str, list] = {}
    for name, f in bk.iter_members(tmp_path / "r.shcbak", PW):
        t = bk._table_of(name)
        if t:
            rows.setdefault(t, []).extend(json.loads(x) for x in f.read().splitlines() if x)
    assert rows["users"] and all(u["password_hash"] == bk.REDACTED_PASSWORD for u in rows["users"])
    assert all(c["token_hash"].startswith("geredigeerd:") for c in rows.get("api_clients", []))
    assert all(i["credentials_enc"] is None for i in rows.get("integrations", []))
    assert not any(s["key"] == "backup_passphrase_enc" for s in rows["settings"])
    async with sessionmaker()() as db:
        await set_secret_setting(db, "backup_passphrase_enc", None)
        await db.commit()


async def test_zigbee2mqtt_unreadable_is_reported_and_alerted(admin, tmp_path, monkeypatch):
    from sqlalchemy import select

    from shc_api.db import sessionmaker
    from shc_api.models import Alert

    z = tmp_path / "zigbee2mqtt"
    (z / "log").mkdir(parents=True)
    (z / "database.db").write_text("{}")
    (z / "configuration.yaml").write_text("network_key: GEHEIM\n")
    (z / "log" / "x.log").write_text("ruis")
    real_open = open

    def fake_open(p, *a, **kw):  # running as root in CI: simulate a 0600 file owned by another user
        if str(p).endswith("configuration.yaml"):
            raise PermissionError(13, "Permission denied", str(p))
        return real_open(p, *a, **kw)

    monkeypatch.setattr(bk, "open", fake_open, raising=False)
    rec = await bk.run_backup(PW, True, backup_dir=tmp_path / "b", z2m_dir=z)
    z2m = rec["detail"]["zigbee2mqtt"]
    assert rec["zigbee2mqtt_included"] is False
    assert z2m["mounted"] and z2m["files"] == 1 and z2m["unreadable"] == ["configuration.yaml"]
    assert "niet (helemaal) leesbaar" in z2m["detail"]
    async with sessionmaker()() as db:
        a = (
            await db.execute(select(Alert).where(Alert.key == bk.Z2M_ALERT_KEY, Alert.resolved_at.is_(None)))
        ).scalar_one()
        assert a.title == "Zigbee2MQTT-gegevens niet in de back-up"
    monkeypatch.setattr(bk, "open", real_open, raising=False)
    rec = await bk.run_backup(PW, True, backup_dir=tmp_path / "b", z2m_dir=z)
    assert rec["zigbee2mqtt_included"] is True and rec["detail"]["zigbee2mqtt"]["files"] == 2  # log/ skipped
    v = bk.verify_file(tmp_path / "b" / rec["filename"], PW)
    assert "zigbee2mqtt/configuration.yaml" in v.manifest["files"]
    out = tmp_path / "terug"
    assert bk.extract_z2m(tmp_path / "b" / rec["filename"], PW, out) == 2
    assert (out / "configuration.yaml").read_text() == "network_key: GEHEIM\n"
    async with sessionmaker()() as db:
        assert (await db.get(Alert, a.id)).resolved_at is not None
