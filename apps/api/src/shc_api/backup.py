"""Portable, encrypted, versioned export (plan §8).

File layout (`.shcbak`):
    b"SHCBAK1\\n" + <header JSON line> + AES-256-GCM(ciphertext of tar.gz)
Header: {"kdf":"scrypt","n":..,"r":8,"p":1,"salt":b64,"nonce":b64,"created_at":..}
The header is authenticated as AAD, so tampering with it fails decryption.

Inside the tar.gz:
    manifest.json            format, version, app version, schema revision, per-file sha256 + row counts
    tables/<table>.jsonl     one JSON object per row
    zigbee2mqtt/...          Zigbee2MQTT data dir (coordinator backup, device db) when mounted

Restore verifies every checksum before touching the database, runs inside a single
transaction, and switches the app to safe mode (no device commands until the user confirms).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
import tarfile
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import DateTime, LargeBinary, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from .config import VERSION
from .models import Base

MAGIC = b"SHCBAK1\n"
FORMAT = "shc-backup"
FORMAT_VERSION = 1
SCRYPT_N = 2**15
SECRET_COLUMNS = {("integrations", "credentials_enc"), ("sessions", "csrf_token")}
SKIP_TABLES = {"sessions", "pairing_codes", "processed_events"}  # short-lived, never exported
MIN_PASSPHRASE = 12


class BackupError(Exception):
    pass


def _enc(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bytes | memoryview):
        return {"$b64": base64.b64encode(bytes(v)).decode()}
    return v


def _key(passphrase: str, salt: bytes, n: int = SCRYPT_N) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=8, p=1).derive(passphrase.encode())


def encrypt(plain: bytes, passphrase: str, created_at: str) -> bytes:
    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"Wachtwoordzin moet minimaal {MIN_PASSPHRASE} tekens zijn.")
    salt, nonce = os.urandom(16), os.urandom(12)
    header = (
        json.dumps(
            {
                "kdf": "scrypt",
                "n": SCRYPT_N,
                "r": 8,
                "p": 1,
                "salt": base64.b64encode(salt).decode(),
                "nonce": base64.b64encode(nonce).decode(),
                "created_at": created_at,
            }
        ).encode()
        + b"\n"
    )
    ct = AESGCM(_key(passphrase, salt)).encrypt(nonce, plain, MAGIC + header)
    return MAGIC + header + ct


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise BackupError("Geen geldig back-upbestand (onbekend formaat).")
    nl = blob.index(b"\n", len(MAGIC))
    header_raw = blob[len(MAGIC) : nl + 1]
    try:
        h = json.loads(header_raw)
        salt, nonce = base64.b64decode(h["salt"]), base64.b64decode(h["nonce"])
        return AESGCM(_key(passphrase, salt, int(h["n"]))).decrypt(nonce, blob[nl + 1 :], MAGIC + header_raw)
    except Exception as exc:
        raise BackupError("Ontsleutelen mislukt: verkeerde wachtwoordzin of beschadigd bestand.") from exc


async def schema_revision(db: AsyncSession) -> str | None:
    try:
        return (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    except Exception:
        return None


async def build_archive(db: AsyncSession, include_secrets: bool, z2m_dir: Path | None = None) -> tuple[bytes, dict]:
    files: dict[str, bytes] = {}
    tables: dict[str, dict] = {}
    for table in Base.metadata.sorted_tables:
        if table.name in SKIP_TABLES:
            continue
        rows = (await db.execute(table.select())).mappings().all()
        lines = []
        for r in rows:
            obj = {k: _enc(v) for k, v in r.items()}
            if not include_secrets:
                for t, c in SECRET_COLUMNS:
                    if t == table.name and c in obj:
                        obj[c] = None
            lines.append(json.dumps(obj, separators=(",", ":"), sort_keys=True))
        data = ("\n".join(lines) + ("\n" if lines else "")).encode()
        name = f"tables/{table.name}.jsonl"
        files[name] = data
        tables[table.name] = {"rows": len(rows), "file": name}
    if z2m_dir and z2m_dir.is_dir():
        for p in sorted(z2m_dir.rglob("*")):
            if p.is_file() and p.stat().st_size < 50_000_000 and "log" not in p.parts:
                files[f"zigbee2mqtt/{p.relative_to(z2m_dir)}"] = p.read_bytes()
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": VERSION,
        "schema_revision": await schema_revision(db),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "secrets_included": include_secrets,
        "zigbee2mqtt_included": any(k.startswith("zigbee2mqtt/") for k in files),
        "tables": tables,
        "checksums": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in [("manifest.json", json.dumps(manifest, indent=2).encode()), *files.items()]:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mtime = 0
            tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue(), manifest


async def export_backup(
    db: AsyncSession, passphrase: str, include_secrets: bool, z2m_dir: Path | None = None
) -> tuple[bytes, dict]:
    plain, manifest = await build_archive(db, include_secrets, z2m_dir)
    return encrypt(plain, passphrase, manifest["created_at"]), manifest


def open_archive(blob: bytes, passphrase: str) -> tuple[dict, dict[str, bytes]]:
    """Decrypt + verify. Raises BackupError on any corruption; never returns partial data."""
    plain = decrypt(blob, passphrase)
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
            for m in tar.getmembers():
                if not m.isfile() or m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise BackupError(f"Onveilig pad in archief: {m.name}")
                f = tar.extractfile(m)
                assert f is not None
                files[m.name] = f.read()
    except tarfile.TarError as exc:
        raise BackupError("Archief beschadigd.") from exc
    if "manifest.json" not in files:
        raise BackupError("manifest.json ontbreekt.")
    manifest = json.loads(files.pop("manifest.json"))
    if manifest.get("format") != FORMAT or manifest.get("format_version", 0) > FORMAT_VERSION:
        raise BackupError("Back-up is van een nieuwere of onbekende versie.")
    sums = manifest.get("checksums", {})
    if set(sums) != set(files):
        raise BackupError("Bestandslijst klopt niet met manifest.")
    for name, data in files.items():
        if hashlib.sha256(data).hexdigest() != sums[name]:
            raise BackupError(f"Checksum klopt niet voor {name}.")
    for t, info in manifest["tables"].items():
        n = files[info["file"]].count(b"\n")
        if n != info["rows"]:
            raise BackupError(f"Aantal records klopt niet voor {t}.")
    return manifest, files


def _dec(col, v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, dict) and "$b64" in v:
        return base64.b64decode(v["$b64"])
    t = col.type
    if isinstance(t, PG_UUID):
        return uuid.UUID(v)
    if isinstance(t, DateTime):
        return dt.datetime.fromisoformat(v)
    if t.__class__.__name__ == "Date":
        return dt.date.fromisoformat(v)
    if t.__class__.__name__ == "Numeric":
        return Decimal(v)
    if isinstance(t, LargeBinary):
        return v
    return v


async def restore(db: AsyncSession, manifest: dict, files: dict[str, bytes]) -> dict[str, int]:
    """Replace all data atomically. Caller commits. Sets safe mode."""
    counts: dict[str, int] = {}
    names = [t.name for t in Base.metadata.sorted_tables]
    await db.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE"))
    for table in Base.metadata.sorted_tables:
        info = manifest["tables"].get(table.name)
        if not info:
            continue
        rows = []
        for line in files[info["file"]].decode().splitlines():
            obj = json.loads(line)
            rows.append({c.name: _dec(c, obj.get(c.name)) for c in table.columns if c.name in obj})
        if rows:
            await db.execute(table.insert(), rows)
        counts[table.name] = len(rows)
    for table in Base.metadata.sorted_tables:  # keep bigserial sequences ahead of restored ids
        pkc = list(table.primary_key.columns)
        if len(pkc) == 1 and pkc[0].autoincrement is True and pkc[0].type.__class__.__name__ == "BigInteger":
            await db.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', '{pkc[0].name}'), "  # noqa: S608 (names from our own metadata)
                    f'COALESCE((SELECT MAX({pkc[0].name}) FROM "{table.name}"), 0) + 1, false)'
                )
            )
    from .store import set_setting

    await set_setting(db, "safe_mode", True)
    await set_setting(
        db, "restored_from", {"created_at": manifest["created_at"], "app_version": manifest["app_version"]}
    )
    return counts
