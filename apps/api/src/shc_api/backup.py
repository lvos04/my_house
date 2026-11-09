"""Portable, encrypted, versioned export (plan §8; verbeterlijst 5, 33, 34).

File format v2 (``.shcbak``), written and read in a streaming way so memory stays flat:

    b"SHCBAK2\\n" + <header JSON line> + chunk_0 + chunk_1 + ... + chunk_last

Header: {"format":2,"kdf":"scrypt","n":..,"r":8,"p":1,"salt":b64,"nonce_prefix":b64(7 bytes),
"chunk_size":1048576,"created_at":..}. Every chunk is AES-256-GCM over at most `chunk_size` bytes
of plaintext (ciphertext = plaintext + 16-byte tag). The nonce is nonce_prefix || chunk counter
(4 bytes, big endian) || last-chunk flag (1 byte), and MAGIC + header is the associated data of
every chunk. So reordering, dropping, truncating or appending chunks, or editing the header, all
fail authentication (the STREAM construction). Files of format v1 (``SHCBAK1``: one AES-GCM blob)
can still be read.

Inside the (encrypted) tar.gz, in this order:
    meta.json                format, versions (app + database schema), created_at, secrets flag
    tables/<table>/NNNN.jsonl  one JSON object per row, at most PART_ROWS rows per part
    zigbee2mqtt/...          Zigbee2MQTT data dir (coordinator backup, device db) when readable
    manifest.json            everything above + per-file sha256, size and row counts

The tables are read with a server-side cursor in one REPEATABLE READ snapshot and written part by
part via a spooled temp file, so a backup of millions of readings needs a few MB of RAM.

Restore verifies every checksum (pass 1) before touching the database. Pass 2 runs in ONE
transaction: truncate, migrate the schema down to the backup's revision when it is older, load
the rows, migrate forward to the current head, fix sequences and switch to safe mode. Backups of a
newer schema are refused. Anything in the backup that does not fit the schema is reported as
dropped.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import hashlib
import io
import json
import logging
import os
import struct
import tarfile
import tempfile
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import MetaData, func, select, text
from sqlalchemy import types as sqltypes
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from .config import VERSION, get_settings
from .models import Base

log = logging.getLogger(__name__)

MAGIC_V1 = b"SHCBAK1\n"
MAGIC = b"SHCBAK2\n"
MAGIC_LEN = len(MAGIC)
FORMAT = "shc-backup"
FORMAT_VERSION = 2
SCRYPT_N = 2**15
CHUNK_SIZE = 1 << 20
TAG = 16
PART_ROWS = 50_000
PART_BYTES = 64 << 20
FETCH_ROWS = 2_000
INSERT_BATCH = 1_000
SPOOL_BYTES = 8 << 20
Z2M_MAX_FILE = 50_000_000
MIN_PASSPHRASE = 12
# Encrypted database dump (verbeterlijst 32): pg_dump --format=custom inside the same encryption as
# a .shcbak file, marked "content": "pg_dump" in the header. BackupRecord.kind "db_dump".
DUMP_CONTENT = "pg_dump"
DUMP_KIND = "db_dump"
PGDMP_MAGIC = b"PGDMP"

# Credentials: dropped from a redacted backup.
SECRET_COLUMNS = {("integrations", "credentials_enc"), ("sessions", "csrf_token")}
# Also replaced in a redacted backup: password hashes and phone-token hashes (verbeterlijst, kleine punten).
REDACTED_PASSWORD = "!geredigeerd"  # noqa: S105 - not a password; can never verify
SECRET_SETTING_KEYS = {"backup_passphrase_enc", "notify_url_enc"}
SKIP_TABLES = {"sessions", "pairing_codes", "processed_events"}  # short-lived, never exported


class BackupError(Exception):
    pass


# --- helpers ----------------------------------------------------------------------------------


def _json_default(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bytes | memoryview):
        return {"$b64": base64.b64encode(bytes(v)).decode()}
    raise TypeError(f"niet te exporteren waarde: {type(v).__name__}")


# Only called for values JSON can not encode itself (uuid, datetime, Decimal, bytes): fast C encoder.
_ENCODER = json.JSONEncoder(separators=(",", ":"), default=_json_default, ensure_ascii=False)


def _key(passphrase: str, salt: bytes, n: int = SCRYPT_N) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=8, p=1).derive(passphrase.encode())


def _check_passphrase(passphrase: str) -> None:
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise BackupError(f"Wachtwoordzin moet minimaal {MIN_PASSPHRASE} tekens zijn.")


def redact_row(table: str, obj: dict) -> dict:
    """Remove secrets from one exported row (redacted backup)."""
    for t, c in SECRET_COLUMNS:
        if t == table and c in obj:
            obj[c] = None
    if table == "users" and "password_hash" in obj:
        obj["password_hash"] = REDACTED_PASSWORD
    if table == "api_clients" and "token_hash" in obj:
        obj["token_hash"] = f"geredigeerd:{obj.get('id', uuid.uuid4())}"
    return obj


# --- format v1 (legacy, read; write kept for tests/tools) ---------------------------------------


def encrypt(plain: bytes, passphrase: str, created_at: str) -> bytes:
    """Legacy v1 format: one AES-GCM blob. Only used to produce v1 files for compatibility tests."""
    _check_passphrase(passphrase)
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
    return MAGIC_V1 + header + AESGCM(_key(passphrase, salt)).encrypt(nonce, plain, MAGIC_V1 + header)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    """Decrypt a v1 file held in memory (v1 files were always built in memory)."""
    if not blob.startswith(MAGIC_V1):
        raise BackupError("Geen geldig back-upbestand (onbekend formaat).")
    nl = blob.index(b"\n", len(MAGIC_V1))
    header_raw = blob[len(MAGIC_V1) : nl + 1]
    try:
        h = json.loads(header_raw)
        salt, nonce = base64.b64decode(h["salt"]), base64.b64decode(h["nonce"])
        return AESGCM(_key(passphrase, salt, int(h["n"]))).decrypt(nonce, blob[nl + 1 :], MAGIC_V1 + header_raw)
    except Exception as exc:
        raise BackupError("Ontsleutelen mislukt: verkeerde wachtwoordzin of beschadigd bestand.") from exc


# --- format v2: chunked AEAD -------------------------------------------------------------------


def _nonce(prefix: bytes, counter: int, last: bool) -> bytes:
    return prefix + struct.pack(">I", counter) + (b"\x01" if last else b"\x00")


class EncryptingWriter:
    """File-like sink: buffers plaintext and writes authenticated chunks to `raw`."""

    def __init__(self, raw, passphrase: str, created_at: str, chunk_size: int = CHUNK_SIZE, content: str | None = None):
        _check_passphrase(passphrase)
        salt = os.urandom(16)
        self._prefix = os.urandom(7)
        header = {
            "format": FORMAT_VERSION,
            "kdf": "scrypt",
            "n": SCRYPT_N,
            "r": 8,
            "p": 1,
            "salt": base64.b64encode(salt).decode(),
            "nonce_prefix": base64.b64encode(self._prefix).decode(),
            "chunk_size": chunk_size,
            "created_at": created_at,
        }
        if content:
            header["content"] = content  # e.g. DUMP_CONTENT; absent = a full export (tar.gz)
        header_raw = json.dumps(header, separators=(",", ":")).encode() + b"\n"
        self._aad = MAGIC + header_raw
        self._aead = AESGCM(_key(passphrase, salt))
        self._raw = raw
        self._cs = chunk_size
        self._buf = bytearray()
        self._counter = 0
        self.sha256 = hashlib.sha256()
        self.size = 0
        self._closed = False
        self._out(self._aad)

    def _out(self, b: bytes) -> None:
        self._raw.write(b)
        self.sha256.update(b)
        self.size += len(b)

    def write(self, data) -> int:
        self._buf += data
        while len(self._buf) > self._cs:  # strictly more: the final chunk is never empty unless no data
            chunk = bytes(self._buf[: self._cs])
            del self._buf[: self._cs]
            self._out(self._aead.encrypt(_nonce(self._prefix, self._counter, False), chunk, self._aad))
            self._counter += 1
        return len(data)

    def flush(self) -> None:
        pass

    def finish(self) -> None:
        if self._closed:
            return
        self._out(self._aead.encrypt(_nonce(self._prefix, self._counter, True), bytes(self._buf), self._aad))
        self._buf.clear()
        self._closed = True


class DecryptingReader(io.RawIOBase):
    """Plaintext stream of a v2 file; raises BackupError on any tampering or truncation."""

    def __init__(self, raw, passphrase: str):
        super().__init__()
        magic = raw.read(MAGIC_LEN)
        if magic != MAGIC:
            raise BackupError("Geen geldig back-upbestand (onbekend formaat).")
        header_raw = raw.readline(4096)
        try:
            h = json.loads(header_raw)
            salt = base64.b64decode(h["salt"])
            self._prefix = base64.b64decode(h["nonce_prefix"])
            self._cs = int(h["chunk_size"])
            n = int(h["n"])
            if len(self._prefix) != 7 or not (1024 <= self._cs <= (64 << 20)) or n > 2**20:
                raise ValueError("header")
        except Exception as exc:
            raise BackupError("Back-upbestand beschadigd (kop onleesbaar).") from exc
        self.header = h
        self._aad = MAGIC + header_raw
        self._aead = AESGCM(_key(passphrase, salt, n))
        self._raw = raw
        self._counter = 0
        self._pending = raw.read(self._cs + TAG)
        self._plain = b""
        self._pos = 0
        self._eof = False
        self._first = True

    def readable(self) -> bool:
        return True

    def _next_chunk(self) -> None:
        cur = self._pending
        if len(cur) < TAG:
            raise BackupError("Back-upbestand is afgekapt of beschadigd.")
        nxt = self._raw.read(self._cs + TAG)
        last = len(nxt) == 0
        try:
            self._plain = self._aead.decrypt(_nonce(self._prefix, self._counter, last), cur, self._aad)
        except InvalidTag as exc:
            if self._first:
                raise BackupError("Ontsleutelen mislukt: verkeerde wachtwoordzin of beschadigd bestand.") from exc
            raise BackupError("Back-upbestand beschadigd (controle mislukt).") from exc
        self._first = False
        self._pos = 0
        self._counter += 1
        self._pending = nxt
        self._eof = last

    def readinto(self, b) -> int:
        while self._pos >= len(self._plain):
            if self._eof:
                return 0
            self._next_chunk()
        n = min(len(b), len(self._plain) - self._pos)
        b[:n] = self._plain[self._pos : self._pos + n]
        self._pos += n
        return n


def open_plaintext(path: Path, passphrase: str) -> tuple[int, io.IOBase]:
    """(format_version, plaintext tar.gz stream) for a v1 or v2 file."""
    f = open(path, "rb")  # noqa: SIM115 - closed by the caller via the returned stream
    try:
        magic = f.read(MAGIC_LEN)
        f.seek(0)
        if magic == MAGIC_V1:
            blob = f.read()
            f.close()
            return 1, io.BytesIO(decrypt(blob, passphrase))
        if magic == MAGIC:
            reader = DecryptingReader(f, passphrase)
            if reader.header.get("content") == DUMP_CONTENT:
                raise BackupError(
                    "Dit bestand is een database-dump (pg_dump), geen volledige export. Zet hem terug met "
                    "'shc-api decrypt-dump' en pg_restore (zie docs/onderhoud.md), of kies een volledige export."
                )
            buffered = io.BufferedReader(reader, buffer_size=CHUNK_SIZE)
            return 2, _Closing(buffered, f)
        raise BackupError("Geen geldig back-upbestand (onbekend formaat).")
    except BaseException:
        f.close()
        raise


class _Closing(io.IOBase):
    def __init__(self, inner, *others):
        self._inner = inner
        self._others = others

    def readable(self) -> bool:
        return True

    def read(self, n=-1):
        return self._inner.read(n)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._inner.close()
        for o in self._others:
            with contextlib.suppress(Exception):
                o.close()
        super().close()


# --- reading members ---------------------------------------------------------------------------


class _Hashing:
    def __init__(self, f):
        self._f = f
        self.sha = hashlib.sha256()
        self.size = 0

    def read(self, n=-1):
        b = self._f.read(n)
        self.sha.update(b)
        self.size += len(b)
        return b


def iter_members(path: Path, passphrase: str, info: dict | None = None) -> Iterator[tuple[str, Any]]:
    """Yield (name, file-like) for every member in archive order. Rejects unsafe entries.
    `info["format_version"]` is set to the file format (1 or 2) when a dict is passed."""
    fmt, plain = open_plaintext(path, passphrase)
    if info is not None:
        info["format_version"] = fmt
    try:
        try:
            with tarfile.open(fileobj=plain, mode="r|gz") as tar:
                for m in tar:
                    if not m.isfile() or m.name.startswith("/") or ".." in Path(m.name).parts:
                        raise BackupError(f"Onveilig pad in archief: {m.name}")
                    f = tar.extractfile(m)
                    assert f is not None
                    yield m.name, f
        except (tarfile.TarError, EOFError, OSError, ValueError) as exc:
            raise BackupError("Archief beschadigd.") from exc
    finally:
        plain.close()


def _lines(f) -> Iterator[bytes]:
    """Lines of a member without loading it whole."""
    rest = b""
    while True:
        block = f.read(1 << 20)
        if not block:
            break
        block = rest + block
        *full, rest = block.split(b"\n")
        yield from full
    if rest:
        yield rest


def _normalize_manifest(m: dict) -> dict:
    """v1 manifests list one file per table; present them like v2."""
    tables = {}
    for t, info in (m.get("tables") or {}).items():
        files = info.get("files") or ([info["file"]] if info.get("file") else [])
        tables[t] = {"rows": int(info.get("rows", 0)), "files": files}
    m["tables"] = tables
    if "files" not in m:
        m["files"] = {k: {"sha256": v} for k, v in (m.get("checksums") or {}).items()}
    return m


@dataclass
class Verified:
    manifest: dict
    format_version: int
    summary: dict = field(default_factory=dict)


SUMMARY_TABLES = {"homes", "devices", "integrations"}


def _secret_setting_row(line: bytes) -> dict | None:
    """The settings row on ``line`` when it is one of the SecretBox-encrypted settings, else None."""
    if b"_enc" not in line:  # cheap pre-filter: every settings row is read, only these few are parsed
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) and row.get("key") in SECRET_SETTING_KEYS else None


def verify_file(path: Path, passphrase: str, collect: bool = True) -> Verified:
    """Pass 1: decrypt and check every member against the manifest without keeping data in memory.
    With `collect`, small tables (homes, devices, integrations) are summarised for the restore wizard,
    and the encrypted settings (backup passphrase, push address) are kept for the key check."""
    _check_passphrase(passphrase)
    seen: dict[str, dict] = {}
    manifest = None
    meta = None
    rows_by_table: dict[str, list[dict]] = {t: [] for t in SUMMARY_TABLES}
    secret_settings: list[dict] = []
    fmt_info: dict = {}
    for name, f in iter_members(path, passphrase, fmt_info):
        if name == "manifest.json":
            manifest = _json_member(f, name)
            continue
        if name == "meta.json":
            raw = f.read(16 << 20)
            seen[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "lines": 0}
            meta = _json_member(io.BytesIO(raw), name)
            continue
        h = _Hashing(f)
        lines = 0
        table = _table_of(name)
        keep = collect and table in SUMMARY_TABLES
        keep_secrets = collect and table == "settings"
        for line in _lines(h):
            if line:
                lines += 1
                if keep:
                    with contextlib.suppress(ValueError):
                        rows_by_table[table].append(json.loads(line))
                elif keep_secrets and (row := _secret_setting_row(line)) is not None:
                    secret_settings.append(row)
        seen[name] = {"sha256": h.sha.hexdigest(), "size": h.size, "lines": lines}
    if manifest is None:
        raise BackupError("manifest.json ontbreekt.")
    manifest = _normalize_manifest(manifest)
    if manifest.get("format") != FORMAT or int(manifest.get("format_version", 0)) > FORMAT_VERSION:
        raise BackupError("Back-up is van een nieuwere of onbekende bestandsversie.")
    if meta and meta.get("schema_revision") != manifest.get("schema_revision"):
        raise BackupError("Back-up beschadigd: versie-informatie klopt niet.")
    expected = manifest["files"]
    if set(expected) != set(seen):
        raise BackupError("Bestandslijst klopt niet met manifest.")
    for name, info in seen.items():
        if info["sha256"] != expected[name].get("sha256"):
            raise BackupError(f"Checksum klopt niet voor {name}.")
    for t, info in manifest["tables"].items():
        n = sum(seen[f]["lines"] for f in info["files"])
        if n != info["rows"]:
            raise BackupError(f"Aantal records klopt niet voor {t}.")
    summary = _summary(rows_by_table) if collect else {}
    if collect:
        summary["_secret_settings"] = secret_settings
    return Verified(manifest, fmt_info.get("format_version", 0), summary)


# What an encrypted setting is, what stops when it does not decrypt, and what to set again (Dutch).
SECRET_SETTING_LABELS = {
    "backup_passphrase_enc": "wachtwoordzin voor automatische back-ups",
    "notify_url_enc": "push-adres voor meldingen buiten de app",
}
_SECRET_SETTING_EFFECTS = {  # (article, what stops, what to set again)
    "backup_passphrase_enc": ("de", "stoppen de automatische back-ups", "de wachtwoordzin"),
    "notify_url_enc": ("het", "komen er geen pushberichten meer", "het push-adres"),
}


def decrypt_check(rows: list[dict], key: str | None = None, settings: list[dict] | None = None) -> dict:
    """Which integration credentials and encrypted settings in a backup decrypt with this server's
    master key (or ``key``).

    ``rows`` are the raw integration rows of ``Verified.summary["_integration_rows"]``, ``settings``
    the rows of ``Verified.summary["_secret_settings"]`` (backup passphrase, push address). A failure
    means the backup was made with another SHC_SECRET_KEY (new NAS, key rotation): after a restore the
    worker cannot use those devices, and automatic backups and push messages stop, until
    ``shc-api rekey --old-key-file`` (or re-pairing and setting the passphrase/address again).
    Returns ``{"ok": n, "failed": ["name (provider)", ...], "total": n, "settings": {key: "ok"|"failed"},
    "settings_failed": [key, ...]}``; ``ok``/``failed``/``total`` count device keys only."""
    from .security import SecretBox

    box = SecretBox(key or get_settings().resolved_secret_key())
    ok, failed = 0, []
    for r in rows:
        enc = r.get("credentials_enc")
        if not enc:
            continue
        try:
            box.decrypt(base64.b64decode(enc["$b64"]) if isinstance(enc, dict) else enc)
            ok += 1
        except Exception:
            failed.append(f"{r.get('name')} ({r.get('provider')})")
    checked: dict[str, str] = {}
    for r in settings or []:
        k = r.get("key")
        raw = (r.get("value") or {}).get("v") if isinstance(r.get("value"), dict) else None
        if k not in SECRET_SETTING_KEYS or not raw:
            continue
        try:
            box.decrypt(base64.b64decode(raw, validate=True))
            checked[k] = "ok"
        except Exception:
            checked[k] = "failed"
    return {
        "ok": ok,
        "failed": failed,
        "total": ok + len(failed),
        "settings": checked,
        "settings_failed": sorted(k for k, v in checked.items() if v == "failed"),
    }


def key_check_warning(check: dict) -> str | None:
    """Dutch restore warning for encrypted settings that do not fit this server's master key."""
    bad = [k for k in check.get("settings_failed") or [] if k in SECRET_SETTING_LABELS]
    if not bad:
        return None
    what = " en ".join(f"{_SECRET_SETTING_EFFECTS[k][0]} {SECRET_SETTING_LABELS[k]}" for k in bad)
    effects = " en ".join(_SECRET_SETTING_EFFECTS[k][1] for k in bad)
    again = " en ".join(_SECRET_SETTING_EFFECTS[k][2] for k in bad)
    fits = "past" if len(bad) == 1 else "passen"
    return (
        f"{what[0].upper()}{what[1:]} in deze back-up {fits} niet bij de hoofdsleutel van deze "
        f"server. Na het terugzetten {effects}, tot je de oude sleutel omzet met 'shc-api rekey --old-key-file' "
        f"of {again} opnieuw instelt bij Systeem."
    )


def _json_member(f, name: str) -> dict:
    try:
        return json.loads(f.read(16 << 20))
    except ValueError as exc:
        raise BackupError(f"{name} beschadigd.") from exc


def _table_of(name: str) -> str | None:
    if not name.startswith("tables/"):
        return None
    rest = name.removeprefix("tables/")
    return rest.split("/")[0] if "/" in rest else rest.removesuffix(".jsonl")


def _summary(rows: dict[str, list[dict]]) -> dict:
    homes = [
        {"id": h.get("id"), "name": h.get("name"), "timezone": h.get("timezone"), "active": h.get("active_to") is None}
        for h in rows["homes"]
    ]
    devices = [
        {
            "id": d.get("id"),
            "home_id": d.get("home_id"),
            "provider": d.get("provider"),
            "kind": d.get("kind"),
            "name": d.get("name"),
            "stable_identity": d.get("stable_identity"),
            "model": d.get("model"),
            "capabilities": d.get("capabilities") or [],
        }
        for d in rows["devices"]
    ]
    integrations = [
        {
            "id": i.get("id"),
            "home_id": i.get("home_id"),
            "provider": i.get("provider"),
            "name": i.get("name"),
            "has_credentials": bool(i.get("credentials_enc")),
        }
        for i in rows["integrations"]
    ]
    # raw integration rows (with encrypted credentials) for a key check; never sent to clients
    return {"homes": homes, "devices": devices, "integrations": integrations, "_integration_rows": rows["integrations"]}


# Backwards-compatible in-memory API (small files only; used by older tools/tests).
def open_archive(blob: bytes, passphrase: str) -> tuple[dict, dict[str, bytes]]:
    with tempfile.NamedTemporaryFile(suffix=".shcbak") as tmp:
        tmp.write(blob)
        tmp.flush()
        v = verify_file(Path(tmp.name), passphrase, collect=False)
        skip = ("manifest.json", "meta.json")
        files = {n: f.read() for n, f in iter_members(Path(tmp.name), passphrase) if n not in skip}
    return v.manifest, files


# --- schema revisions ----------------------------------------------------------------------------


async def schema_revision(db: AsyncSession | AsyncConnection) -> str | None:
    try:
        return (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    except Exception:
        return None


def alembic_config(url: str | None = None, connection=None):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def known_revisions() -> list[str]:
    """Revisions from oldest to newest (head last)."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    return [r.revision for r in reversed(list(script.walk_revisions()))]


def head_revision() -> str:
    return known_revisions()[-1]


def schema_relation(rev: str | None) -> str:
    """'same', 'older', 'newer' (unknown revision: made by a newer app) or 'unknown' (none recorded)."""
    if not rev:
        return "unknown"
    revs = known_revisions()
    if rev == revs[-1]:
        return "same"
    return "older" if rev in revs else "newer"


# --- writing --------------------------------------------------------------------------------------


@dataclass
class _Part:
    name: str
    rows: int
    sha256: str
    size: int


class _PartWriter:
    """Writes one table as JSONL parts via spooled temp files (RAM up to SPOOL_BYTES, then disk)."""

    def __init__(self, tar: tarfile.TarFile, table: str, tmpdir: Path | None):
        self.tar = tar
        self.table = table
        self.tmpdir = tmpdir
        self.parts: list[_Part] = []
        self.rows = 0
        self._new()

    def _new(self) -> None:
        self.spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES, dir=self.tmpdir)  # noqa: SIM115
        self.sha = hashlib.sha256()
        self.part_rows = 0

    def add(self, line: bytes) -> None:
        self.spool.write(line)
        self.sha.update(line)
        self.part_rows += 1
        self.rows += 1
        if self.part_rows >= PART_ROWS or self.spool.tell() >= PART_BYTES:
            self.flush()
            self._new()

    def flush(self, force: bool = False) -> None:
        if self.part_rows == 0 and not (force and not self.parts):
            self.spool.close()
            return
        name = f"tables/{self.table}/{len(self.parts) + 1:04d}.jsonl"
        size = self.spool.tell()
        self.spool.seek(0)
        ti = tarfile.TarInfo(name)
        ti.size = size
        ti.mtime = 0
        ti.mode = 0o600
        self.tar.addfile(ti, self.spool)
        self.spool.close()
        self.parts.append(_Part(name, self.part_rows, self.sha.hexdigest(), size))


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    ti.mtime = 0
    ti.mode = 0o600
    tar.addfile(ti, io.BytesIO(data))


# Zigbee2MQTT's secrets file (``!secret.yaml mqtt_password`` in configuration.yaml): holds the MQTT
# password in plain text. A redacted backup keeps its keys but never its values.
Z2M_SECRET_FILES = {"secret.yaml", "secret.yml"}


def redact_z2m_secrets(raw: bytes) -> bytes:
    """Every top-level key of a Zigbee2MQTT secrets file with an empty value; nothing else is kept
    (no values, comments or nested lines), so no secret can slip through a YAML form we did not
    expect. Zigbee2MQTT still finds each key after a restore; fill in the passwords again."""
    keys: list[str] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line or line[0] in " \t#-" or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if key and key not in keys:
            keys.append(key)
    head = "# Geredigeerde back-up: de waarden zijn weggelaten. Vul ze opnieuw in (mqtt_password: zie secrets/).\n"
    return (head + "".join(f'{k}: ""\n' for k in keys)).encode()


def _add_z2m(tar: tarfile.TarFile, z2m_dir: Path | None, files: dict, redact: bool = False) -> dict:
    """Add the Zigbee2MQTT data dir; report clearly whether it was readable (verbeterlijst 33).
    With ``redact`` (a backup without secrets) secret.yaml goes in without its values."""
    report = {"mounted": False, "included": False, "files": 0, "unreadable": [], "redacted": [], "detail": ""}
    if z2m_dir is None or not z2m_dir.exists():
        report["detail"] = "Geen Zigbee2MQTT-map gekoppeld."
        return report
    report["mounted"] = True
    errors: list[str] = []

    def onerror(exc: OSError) -> None:
        errors.append(str(Path(exc.filename).relative_to(z2m_dir)) if exc.filename else str(exc))

    if not os.access(z2m_dir, os.R_OK | os.X_OK):
        errors.append(".")
    else:
        for root, dirs, names in os.walk(z2m_dir, onerror=onerror):
            dirs[:] = sorted(d for d in dirs if d != "log")
            for n in sorted(names):
                p = Path(root) / n
                rel = p.relative_to(z2m_dir)
                try:
                    st = p.stat()
                    if not p.is_file() or st.st_size >= Z2M_MAX_FILE:
                        continue
                    if redact and rel.as_posix() in Z2M_SECRET_FILES:
                        data = redact_z2m_secrets(p.read_bytes())
                        name = f"zigbee2mqtt/{rel.as_posix()}"
                        _add_bytes(tar, name, data)
                        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                        report["files"] += 1
                        report["redacted"].append(rel.as_posix())
                        continue
                    with open(p, "rb") as fh:
                        h = _Hashing(fh)
                        name = f"zigbee2mqtt/{rel.as_posix()}"
                        ti = tarfile.TarInfo(name)
                        ti.size = st.st_size
                        ti.mtime = 0
                        ti.mode = 0o600
                        tar.addfile(ti, h)
                        if h.size != st.st_size:
                            raise BackupError(f"Bestand veranderde tijdens de back-up: {rel}")
                        files[name] = {"sha256": h.sha.hexdigest(), "size": h.size}
                        report["files"] += 1
                except PermissionError:
                    errors.append(str(rel))
    report["unreadable"] = errors
    report["included"] = report["files"] > 0 and not errors
    if errors:
        report["detail"] = (
            "Zigbee2MQTT-map is gekoppeld maar niet (helemaal) leesbaar voor de back-up: "
            + ", ".join(errors[:5])
            + ". Geef groep 10001 leesrechten (zie installatie)."
        )
    elif report["files"] == 0:
        report["detail"] = "Zigbee2MQTT-map is leeg."
    else:
        report["detail"] = f"{report['files']} bestanden van Zigbee2MQTT opgenomen."
        if report["redacted"]:
            report["detail"] += (
                f" {', '.join(report['redacted'])} zonder wachtwoorden (geredigeerde back-up): vul na een herstel "
                "mqtt_password weer in (de waarde staat in secrets/mqtt_z2m_password)."
            )
    return report


async def write_backup(
    conn: AsyncConnection,
    path: Path,
    passphrase: str,
    include_secrets: bool,
    z2m_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Stream the whole database into an encrypted backup file at `path`; returns the manifest.

    `conn` must not be in a transaction; a REPEATABLE READ snapshot is used for consistency."""
    _check_passphrase(passphrase)
    created_at = dt.datetime.now(dt.UTC).isoformat()
    rev = await schema_revision(conn)
    if conn.in_transaction():
        await conn.rollback()
    meta = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": VERSION,
        "schema_revision": rev,
        "created_at": created_at,
        "secrets_included": include_secrets,
    }
    files: dict[str, dict] = {}
    tables: dict[str, dict] = {}
    tmpdir = path.parent
    # Blocking file I/O on purpose: backups run in their own process (shc-api backup), not in the API loop.
    with open(path, "wb") as raw:  # noqa: ASYNC230
        enc = EncryptingWriter(raw, passphrase, created_at)
        with tarfile.open(fileobj=enc, mode="w|gz", compresslevel=6) as tar:
            meta_raw = json.dumps(meta, indent=2).encode()
            _add_bytes(tar, "meta.json", meta_raw)
            files["meta.json"] = {"sha256": hashlib.sha256(meta_raw).hexdigest(), "size": len(meta_raw)}
            snap = await conn.execution_options(isolation_level="REPEATABLE READ")
            async with snap.begin():
                await snap.execute(text("SET TRANSACTION READ ONLY"))
                for table in Base.metadata.sorted_tables:
                    if table.name in SKIP_TABLES:
                        continue
                    if progress:
                        progress(table.name)
                    pw = _PartWriter(tar, table.name, tmpdir)
                    result = await snap.stream(table.select().execution_options(yield_per=FETCH_ROWS))
                    keys = list(result.keys())
                    redact = not include_secrets
                    async for partition in result.partitions(FETCH_ROWS):
                        for r in partition:
                            obj = dict(zip(keys, r, strict=True))
                            if table.name == "backups" and obj.get("status") == "running":
                                continue  # in-progress backups (this one too) mean nothing after a restore
                            if redact:
                                if table.name == "settings" and obj.get("key") in SECRET_SETTING_KEYS:
                                    continue
                                obj = redact_row(table.name, obj)
                            pw.add(_ENCODER.encode(obj).encode() + b"\n")
                    pw.flush(force=True)
                    for p in pw.parts:
                        files[p.name] = {"sha256": p.sha256, "size": p.size, "rows": p.rows}
                    tables[table.name] = {"rows": pw.rows, "files": [p.name for p in pw.parts]}
            z2m = _add_z2m(tar, z2m_dir, files, redact=not include_secrets)
            manifest = {
                **meta,
                "redacted": not include_secrets,
                "zigbee2mqtt_included": z2m["included"],
                "zigbee2mqtt": z2m,
                "tables": tables,
                "files": files,
                "checksums": {k: v["sha256"] for k, v in files.items()},
            }
            _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=2).encode())
        enc.finish()
        raw.flush()
        os.fsync(raw.fileno())
    manifest["file_sha256"] = enc.sha256.hexdigest()
    manifest["file_size"] = enc.size
    return manifest


# --- running a backup (CLI / worker subprocess) ------------------------------------------------


def backup_filename(auto: bool) -> str:
    stamp = f"{dt.datetime.now(dt.UTC):%Y%m%d-%H%M%S}-{os.urandom(2).hex()}"  # unique within a second
    return f"shc-{'auto-' if auto else ''}{stamp}.shcbak"


async def run_backup(
    passphrase: str,
    include_secrets: bool = True,
    *,
    auto: bool = False,
    record_id: uuid.UUID | None = None,
    url: str | None = None,
    backup_dir: Path | None = None,
    z2m_dir: Path | None = None,
) -> dict:
    """Make a backup file + its `backups` row (status running -> ok/failed). Streams everything;
    the written file is decrypted and verified again before it is reported as ok."""
    from .models import BackupRecord

    _check_passphrase(passphrase)
    s = get_settings()
    backup_dir = backup_dir or s.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    if z2m_dir is None:
        cand = s.data_dir / "zigbee2mqtt"
        z2m_dir = cand if cand.exists() else None
    engine = create_async_engine(url or s.database_url, poolclass=NullPool)
    name = backup_filename(auto)
    path = backup_dir / name
    tmp = backup_dir / f".{name}.tmp"
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            rec = await db.get(BackupRecord, record_id) if record_id else None
            if rec is None:
                rec = BackupRecord(id=record_id or uuid.uuid4(), kind="export", filename=name, status="running")
                db.add(rec)
            rec.filename, rec.status, rec.auto = name, "running", auto
            rec.app_version = VERSION
            rec.detail = {"secrets_included": include_secrets}
            await db.commit()
            rid = rec.id
        try:
            async with engine.connect() as conn:
                manifest = await write_backup(conn, tmp, passphrase, include_secrets, z2m_dir)
            verify_file(tmp, passphrase, collect=False)  # restore test light: the written file opens
            tmp.replace(path)
        except BaseException as exc:
            tmp.unlink(missing_ok=True)
            msg = str(exc) if isinstance(exc, BackupError) else f"Back-up mislukt: {exc.__class__.__name__}: {exc}"
            async with AsyncSession(engine, expire_on_commit=False) as db:
                rec = await db.get(BackupRecord, rid)
                rec.status = "failed"
                rec.detail = {**(rec.detail or {}), "error": msg[:500]}
                await db.commit()
            raise BackupError(msg) from exc
        async with AsyncSession(engine, expire_on_commit=False) as db:
            rec = await db.get(BackupRecord, rid)
            rec.status = "ok"
            rec.size_bytes = manifest["file_size"]
            rec.sha256 = manifest["file_sha256"]
            rec.schema_revision = manifest["schema_revision"]
            rec.app_version = manifest["app_version"]
            rec.detail = {
                "tables": {k: v["rows"] for k, v in manifest["tables"].items()},
                "secrets_included": include_secrets,
                "zigbee2mqtt_included": manifest["zigbee2mqtt_included"],
                "zigbee2mqtt": manifest["zigbee2mqtt"],
                "format_version": FORMAT_VERSION,
            }
            await z2m_alert(db, manifest["zigbee2mqtt"])
            await db.commit()
            return record_dict(rec)
    finally:
        await engine.dispose()


# --- encrypted database dump (verbeterlijst 32) ---------------------------------------------------


def dump_filename(auto: bool) -> str:
    stamp = f"{dt.datetime.now(dt.UTC):%Y%m%d-%H%M%S}-{os.urandom(2).hex()}"
    return f"shc-{'auto-' if auto else ''}dump-{stamp}.shcdump"


def _pg_dump_sync(url: str, target: Path, passphrase: str, created_at: str) -> dict:
    """Stream ``pg_dump --format=custom`` through the backup encryption into ``target``."""
    import shutil
    import subprocess  # noqa: S404 - fixed executable, no shell

    from .cli import _libpq

    exe = shutil.which("pg_dump")
    if not exe:
        raise BackupError("pg_dump niet gevonden: database-dump niet mogelijk.")
    dsn, pw = _libpq(url)
    env = dict(os.environ)
    if pw:
        env["PGPASSWORD"] = pw
    with open(target, "wb") as raw:
        enc = EncryptingWriter(raw, passphrase, created_at, content=DUMP_CONTENT)
        proc = subprocess.Popen(  # noqa: S603 - fixed executable, no shell
            [exe, "--format=custom", "--no-owner", "--no-privileges", f"--dbname={dsn}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        assert proc.stdout is not None
        plain = 0
        while block := proc.stdout.read(CHUNK_SIZE):
            enc.write(block)
            plain += len(block)
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        code = proc.wait()
        if code != 0:
            raise BackupError(f"pg_dump mislukt: {err.strip()[:300] or code}")
        enc.finish()
        raw.flush()
        os.fsync(raw.fileno())
    return {"file_sha256": enc.sha256.hexdigest(), "file_size": enc.size, "plain_bytes": plain}


def verify_dump(path: Path, passphrase: str) -> int:
    """Decrypt a dump completely (every chunk is authenticated) and check it is a pg_dump archive.
    Returns the plaintext size; raises BackupError."""
    with open(path, "rb") as f:
        reader = DecryptingReader(f, passphrase)
        if reader.header.get("content") != DUMP_CONTENT:
            raise BackupError("Dit bestand is geen database-dump.")
        stream = io.BufferedReader(reader, buffer_size=CHUNK_SIZE)
        head = stream.read(len(PGDMP_MAGIC))
        if head != PGDMP_MAGIC:
            raise BackupError("Database-dump beschadigd (geen pg_dump-archief).")
        n = len(head)
        while block := stream.read(CHUNK_SIZE):
            n += len(block)
    return n


def decrypt_dump(path: Path, passphrase: str, out: Path) -> int:
    """Write the plain pg_dump archive of an encrypted dump to ``out`` (for pg_restore)."""
    _check_passphrase(passphrase)
    tmp = out.with_name(f".{out.name}.tmp")
    try:
        with open(path, "rb") as f, open(tmp, "wb") as dst:
            reader = DecryptingReader(f, passphrase)
            if reader.header.get("content") != DUMP_CONTENT:
                raise BackupError("Dit bestand is geen database-dump; gebruik 'shc-api restore' voor een export.")
            stream = io.BufferedReader(reader, buffer_size=CHUNK_SIZE)
            n = 0
            while block := stream.read(CHUNK_SIZE):
                dst.write(block)
                n += len(block)
        os.chmod(tmp, 0o600)
        tmp.replace(out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return n


async def run_db_dump(
    passphrase: str,
    *,
    auto: bool = True,
    url: str | None = None,
    backup_dir: Path | None = None,
) -> dict:
    """Make an encrypted database dump + its ``backups`` row (kind "db_dump"). Lighter than a full
    export (no Zigbee2MQTT files, restored with pg_restore instead of the app). Verified after writing."""
    from .models import BackupRecord

    _check_passphrase(passphrase)
    s = get_settings()
    db_url = url or s.database_url
    backup_dir = backup_dir or s.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(db_url, poolclass=NullPool)
    name = dump_filename(auto)
    path = backup_dir / name
    tmp = backup_dir / f".{name}.tmp"
    created_at = dt.datetime.now(dt.UTC).isoformat()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            rev = await schema_revision(db)
            rec = BackupRecord(
                kind=DUMP_KIND,
                filename=name,
                status="running",
                auto=auto,
                app_version=VERSION,
                schema_revision=rev,
                detail={"content": DUMP_CONTENT, "secrets_included": True},
            )
            db.add(rec)
            await db.commit()
            rid = rec.id
        try:
            info = await asyncio.to_thread(_pg_dump_sync, db_url, tmp, passphrase, created_at)
            await asyncio.to_thread(verify_dump, tmp, passphrase)
            tmp.replace(path)
        except BaseException as exc:
            tmp.unlink(missing_ok=True)
            msg = (
                str(exc) if isinstance(exc, BackupError) else f"Database-dump mislukt: {exc.__class__.__name__}: {exc}"
            )
            async with AsyncSession(engine, expire_on_commit=False) as db:
                rec = await db.get(BackupRecord, rid)
                rec.status = "failed"
                rec.detail = {**(rec.detail or {}), "error": msg[:500]}
                await db.commit()
            raise BackupError(msg) from exc
        async with AsyncSession(engine, expire_on_commit=False) as db:
            rec = await db.get(BackupRecord, rid)
            rec.status = "ok"
            rec.size_bytes = info["file_size"]
            rec.sha256 = info["file_sha256"]
            rec.detail = {
                "content": DUMP_CONTENT,
                "secrets_included": True,
                "plain_bytes": info["plain_bytes"],
                "restore": "shc-api decrypt-dump <bestand> --out <dump>; daarna pg_restore --clean --if-exists",
            }
            await db.commit()
            return record_dict(rec)
    finally:
        await engine.dispose()


Z2M_ALERT_KEY = "backup_zigbee2mqtt"


async def z2m_alert(db: AsyncSession, z2m: dict) -> None:
    """Alert when the Zigbee2MQTT dir is mounted but (partly) unreadable; resolve it once it is included."""
    from .models import Home
    from .store import raise_alert, resolve_alert

    if z2m.get("mounted") and z2m.get("unreadable"):
        home_id = (
            await db.execute(select(Home.id).where(Home.active_to.is_(None)).order_by(Home.active_from).limit(1))
        ).scalar_one_or_none()
        await raise_alert(
            db, home_id, Z2M_ALERT_KEY, "warn", "Zigbee2MQTT-gegevens niet in de back-up", z2m["detail"], "backup"
        )
    elif z2m.get("included") or not z2m.get("mounted"):
        await resolve_alert(db, Z2M_ALERT_KEY)


def record_dict(b) -> dict:
    d = b.detail or {}
    return {
        "id": b.id,
        "created_at": b.created_at,
        "kind": b.kind,
        "auto": bool(b.auto),
        "filename": b.filename,
        "size_bytes": b.size_bytes,
        "sha256": b.sha256,
        "checksum": b.sha256,
        "status": b.status,
        "app_version": b.app_version,
        "schema_revision": b.schema_revision,
        "secrets_included": d.get("secrets_included"),
        "zigbee2mqtt_included": d.get("zigbee2mqtt_included"),
        "error": d.get("error"),
        "detail": d,
    }


# --- restoring ------------------------------------------------------------------------------------


def _dec(col, v: Any) -> Any:
    if v is None:
        return None
    t = col.type
    if isinstance(t, sqltypes.LargeBinary):
        if isinstance(v, dict) and "$b64" in v:
            return base64.b64decode(v["$b64"])
        return v.encode() if isinstance(v, str) else v
    if isinstance(v, str):
        if isinstance(t, sqltypes.Uuid) or t.__class__.__name__ == "UUID":
            return uuid.UUID(v)
        if isinstance(t, sqltypes.DateTime):
            return dt.datetime.fromisoformat(v)
        if isinstance(t, sqltypes.Date):
            return dt.date.fromisoformat(v)
        if isinstance(t, sqltypes.Numeric) and not isinstance(t, sqltypes.Float):
            return Decimal(v)
    return v


@dataclass
class RestoreReport:
    schema_from: str | None = None
    schema_to: str | None = None
    migrated: bool = False
    counts: dict = field(default_factory=dict)
    dropped: dict = field(default_factory=lambda: {"tables": {}, "columns": {}, "rows_removed_by_migration": {}})
    mode: str = "same_home"
    new_home_id: str | None = None
    archived_homes: list = field(default_factory=list)
    zigbee2mqtt_included: bool = False
    # new_home: comfort profiles and schedules taken along as drafts (plan §8.5)
    drafts: dict | None = None

    def as_dict(self) -> dict:
        return {
            "schema_from": self.schema_from,
            "schema_to": self.schema_to,
            "migrated": self.migrated,
            "counts": self.counts,
            "total_records": sum(self.counts.values()),
            "dropped": self.dropped,
            "mode": self.mode,
            "new_home_id": self.new_home_id,
            "archived_homes": self.archived_homes,
            "zigbee2mqtt_included": self.zigbee2mqtt_included,
            "drafts": self.drafts,
        }


def check_restorable(manifest: dict) -> str:
    """Refuse backups of a newer (or unknown) schema; returns the relation."""
    rel = schema_relation(manifest.get("schema_revision"))
    if rel == "newer":
        raise BackupError(
            f"Deze back-up is gemaakt met een nieuwere versie (app {manifest.get('app_version')}, "
            f"schema {manifest.get('schema_revision')}). Werk de app eerst bij naar die versie of nieuwer."
        )
    if rel == "unknown":
        raise BackupError("Deze back-up vermeldt geen databaseversie en kan niet veilig hersteld worden.")
    return rel


def _restore_sync(conn, path: Path, passphrase: str, manifest: dict, mode: str, report: RestoreReport) -> None:
    from alembic import command

    rev = manifest["schema_revision"]
    head = head_revision()
    report.schema_from, report.schema_to = rev, head
    conn.execute(text("SET LOCAL lock_timeout = '60s'"))
    current = MetaData()
    current.reflect(bind=conn)
    names = [t for t in current.tables if t != "alembic_version"]
    if names:
        conn.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE"))
    db_rev = None
    if conn.execute(text("SELECT to_regclass('alembic_version')")).scalar() is not None:
        db_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    cfg = alembic_config(connection=conn)
    if db_rev != rev:
        # Load the rows into the schema they were written with; migrate forward afterwards.
        if _is_older(rev, db_rev):
            command.downgrade(cfg, rev)
        else:
            command.upgrade(cfg, rev)
        report.migrated = True
    target = MetaData()
    target.reflect(bind=conn)
    batch: list[dict] = []
    table = None
    skipped_cols: set[str] = set()
    for name, f in iter_members(path, passphrase):
        tname = _table_of(name)
        if tname is None:
            continue
        if tname not in target.tables or tname == "alembic_version":
            n = sum(1 for line in _lines(f) if line)
            report.dropped["tables"][tname] = report.dropped["tables"].get(tname, 0) + n
            continue
        if table is None or table.name != tname:
            _flush(conn, table, batch, report)
            batch = []
            table = target.tables[tname]
            skipped_cols = set()
        cols = table.columns
        for line in _lines(f):
            if not line:
                continue
            obj = json.loads(line)
            row = {}
            for k, v in obj.items():
                c = cols.get(k)
                if c is None:
                    if k not in skipped_cols:
                        skipped_cols.add(k)
                        report.dropped["columns"].setdefault(tname, []).append(k)
                    continue
                row[k] = _dec(c, v)
            batch.append(row)
            if len(batch) >= INSERT_BATCH:
                _flush(conn, table, batch, report)
                batch = []
    _flush(conn, table, batch, report)
    if rev != head:
        command.upgrade(cfg, "head")
        report.migrated = True
        final = MetaData()
        final.reflect(bind=conn)
        for t, n in report.counts.items():
            if t in final.tables:
                now = conn.execute(select(func.count()).select_from(final.tables[t])).scalar_one()
                if now < n:
                    report.dropped["rows_removed_by_migration"][t] = n - now
    for table in Base.metadata.sorted_tables:  # keep bigserial sequences ahead of restored ids
        pkc = list(table.primary_key.columns)
        if len(pkc) == 1 and pkc[0].autoincrement is True and isinstance(pkc[0].type, sqltypes.BigInteger):
            conn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', '{pkc[0].name}'), "  # noqa: S608 (own metadata)
                    f'COALESCE((SELECT MAX({pkc[0].name}) FROM "{table.name}"), 0) + 1, false)'
                )
            )
    _after_restore(conn, manifest, mode, report)


def _is_older(rev: str, than: str | None) -> bool:
    revs = known_revisions()
    return than is not None and than in revs and revs.index(rev) < revs.index(than)


def _flush(conn, table, batch: list[dict], report: RestoreReport) -> None:
    if table is None:
        return
    report.counts.setdefault(table.name, 0)
    if not batch:
        return
    conn.execute(table.insert(), batch)
    report.counts[table.name] += len(batch)


def _set(conn, key: str, value: Any) -> None:
    conn.execute(
        text(
            "INSERT INTO settings (key, value) VALUES (:k, CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": key, "v": json.dumps({"v": value}, default=str)},
    )


# Comfort settings a zone takes along to a new home (plan §8.5); calibration (sensor correction,
# learned start times, adaptive model, unit limits) starts again.
DRAFT_POLICY_KEYS = ("band_c", "energy_priority", "override_default_minutes")


def _copy_comfort_drafts(conn, old_home_ids: list, new_home_id: uuid.UUID, new_zones: dict[str, uuid.UUID]) -> dict:
    """Plan §8.5, new home: comfort profiles and zone schedules stay available as drafts.

    Profiles are copied as they are (a profile does nothing until a zone follows it). A zone's
    schedule goes to the new zone with the same name (case-insensitive), with its profile link and
    comfort settings; that zone starts in control "off", so nothing runs until the user chooses
    "Terug naar schema". Schedules of zones without a namesake stay with the archived home."""
    profiles = conn.execute(
        text(
            "SELECT p.id, p.name, p.target_c, p.band_c, p.mode, p.energy_priority, p.schedule "
            "FROM comfort_profiles p JOIN homes h ON h.id = p.home_id "
            "WHERE p.home_id = ANY(:ids) ORDER BY h.active_from, p.created_at"
        ),
        {"ids": list(old_home_ids)},
    ).all()
    taken: set[str] = set()
    profile_map: dict = {}
    for p in profiles:
        name, n = p.name, 2
        while name.lower() in taken:  # two archived homes with the same profile name
            suffix = f" ({n})"
            name, n = p.name[: 64 - len(suffix)] + suffix, n + 1
        taken.add(name.lower())
        new_pid = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO comfort_profiles (id, home_id, name, target_c, band_c, mode, energy_priority, schedule, "
                "created_at) VALUES (:id, :h, :n, :t, :b, :m, :e, CAST(:s AS jsonb), now())"
            ),
            {
                "id": new_pid,
                "h": new_home_id,
                "n": name,
                "t": p.target_c,
                "b": p.band_c,
                "m": p.mode,
                "e": p.energy_priority,
                "s": json.dumps(p.schedule or []),
            },
        )
        profile_map[p.id] = new_pid
    zones = conn.execute(
        text(
            "SELECT z.id, z.name, z.profile, z.profile_id, z.policy "
            "FROM climate_zones z JOIN homes h ON h.id = z.home_id "
            "WHERE z.home_id = ANY(:ids) ORDER BY h.active_from, z.name"
        ),
        {"ids": list(old_home_ids)},
    ).all()
    by_name = {k.lower(): v for k, v in new_zones.items()}
    filled: set = set()
    out_zones = []
    for z in zones:
        blocks = conn.execute(
            text("SELECT days, start, target_c, mode, label FROM schedule_blocks WHERE zone_id = :z ORDER BY start"),
            {"z": z.id},
        ).all()
        target = by_name.get((z.name or "").lower())
        if not blocks and z.profile_id is None:
            continue  # nothing to take along
        if target is None or target in filled:
            out_zones.append({"name": z.name, "blocks": len(blocks), "copied_to": None})
            continue
        filled.add(target)
        for b in blocks:
            conn.execute(
                text(
                    "INSERT INTO schedule_blocks (id, zone_id, days, start, target_c, mode, label) "
                    "VALUES (:id, :z, CAST(:d AS jsonb), :s, :t, :m, :l)"
                ),
                {
                    "id": uuid.uuid4(),
                    "z": target,
                    "d": json.dumps(b.days),
                    "s": b.start,
                    "t": b.target_c,
                    "m": b.mode,
                    "l": b.label or "",
                },
            )
        policy = {k: v for k, v in (z.policy or {}).items() if k in DRAFT_POLICY_KEYS}
        conn.execute(
            text(
                "UPDATE climate_zones SET control = 'off', profile = :p, profile_id = :pid, "
                "policy = CAST(:pol AS jsonb) WHERE id = :id"
            ),
            {"p": z.profile, "pid": profile_map.get(z.profile_id), "pol": json.dumps(policy), "id": target},
        )
        conn.execute(
            text("INSERT INTO zone_log (zone_id, at, kind, message) VALUES (:z, now(), 'config', :m)"),
            {
                "z": target,
                "m": "Schema overgenomen uit de vorige woning als concept. De zone staat uit; kies "
                "'Terug naar schema' zodra de airco gekoppeld en getest is.",
            },
        )
        out_zones.append({"name": z.name, "blocks": len(blocks), "copied_to": str(target)})
    return {"profiles": len(profile_map), "zones": out_zones}


def _after_restore(conn, manifest: dict, mode: str, report: RestoreReport) -> None:
    """Safe mode, maintenance off, a device snapshot for the compatibility check and, for a move to a
    new home, archive the restored home(s) (integrations off) and create a fresh one."""
    now = dt.datetime.now(dt.UTC)
    report.mode = mode
    devices = conn.execute(
        text("SELECT id, provider, kind, name, stable_identity, capabilities, state, online, home_id FROM devices")
    ).all()
    snapshot = {
        str(d.id): {
            "provider": d.provider,
            "kind": d.kind,
            "name": d.name,
            "stable_identity": d.stable_identity,
            "capabilities": d.capabilities or [],
            "caps": (d.state or {}).get("caps"),
            "home_id": str(d.home_id),
        }
        for d in devices
    }
    if mode == "new_home":
        from .routers.homes import DEFAULT_ROLES

        homes = conn.execute(
            text("SELECT id, name, timezone, currency FROM homes WHERE active_to IS NULL ORDER BY active_from")
        ).all()
        first = None
        for h in homes:
            conn.execute(text("UPDATE homes SET active_to = :t WHERE id = :id"), {"t": now, "id": h.id})
            conn.execute(text("UPDATE integrations SET enabled = false WHERE home_id = :id"), {"id": h.id})
            report.archived_homes.append(str(h.id))
            first = first or h
        new_id = uuid.uuid4()
        name = (first.name if first else "Thuis") + " (nieuw)"
        conn.execute(
            text("INSERT INTO homes (id, name, timezone, currency, active_from) VALUES (:id, :n, :tz, :c, :t)"),
            {
                "id": new_id,
                "n": name[:100],
                "tz": first.timezone if first else "Europe/Amsterdam",
                "c": first.currency if first else "EUR",
                "t": now,
            },
        )
        for key, label, protected in DEFAULT_ROLES:
            conn.execute(
                text("INSERT INTO logical_roles (id, home_id, key, label, protected) VALUES (:id, :h, :k, :l, :p)"),
                {"id": uuid.uuid4(), "h": new_id, "k": key, "l": label, "p": protected},
            )
        new_zones: dict[str, uuid.UUID] = {}
        for room_name, function in (("Woonkamer", "living"), ("Slaapkamer", "bedroom")):
            rid = uuid.uuid4()
            conn.execute(
                text("INSERT INTO rooms (id, home_id, name, function) VALUES (:id, :h, :n, :f)"),
                {"id": rid, "h": new_id, "n": room_name, "f": function},
            )
            zt = Base.metadata.tables["climate_zones"]
            zid = uuid.uuid4()
            conn.execute(
                zt.insert().values(
                    id=zid,
                    home_id=new_id,
                    name=room_name,
                    room_id=rid,
                    outdoor_group="multisplit-1",
                    profile="Comfort",
                    policy={},
                    control="schedule",
                    desired_mode="off",
                    last_commanded={},
                    start_times=[],
                    adaptive_enabled=False,
                )
            )
            new_zones[room_name] = zid
        report.drafts = _copy_comfort_drafts(conn, [h.id for h in homes], new_id, new_zones)
        report.new_home_id = str(new_id)
    report.zigbee2mqtt_included = bool(manifest.get("zigbee2mqtt_included"))
    conn.execute(
        text(
            "UPDATE backups SET status = 'failed', "
            "detail = detail || jsonb_build_object('error', 'Niet afgerond op het moment van de back-up.') "
            "WHERE status = 'running'"
        )
    )
    # An automatic backup was running when this one was made (its own "running_since"): that attempt
    # belongs to the old server and is no interrupted backup here (worker/backup_job.py).
    conn.execute(
        text(
            "UPDATE settings SET value = jsonb_set(value, '{v}', (value -> 'v') - 'running_since' - 'running_kind') "
            "WHERE key = 'backup_state' AND jsonb_typeof(value -> 'v') = 'object'"
        )
    )
    _set(conn, "safe_mode", True)
    _set(conn, "maintenance", False)
    _set(
        conn,
        "restored_from",
        {
            "created_at": manifest.get("created_at"),
            "app_version": manifest.get("app_version"),
            "schema_revision": manifest.get("schema_revision"),
            "restored_at": now.isoformat(),
            "mode": mode,
        },
    )
    _set(conn, "restore_snapshot", {"restored_at": now.isoformat(), "devices": snapshot})
    _set(conn, "restore_report", report.as_dict())


async def restore_file(
    path: Path,
    passphrase: str,
    *,
    url: str | None = None,
    mode: str = "same_home",
    verified: Verified | None = None,
) -> RestoreReport:
    """Verify (pass 1) and restore (pass 2) in one transaction. Caller handles maintenance mode."""
    if mode not in ("same_home", "new_home"):
        raise BackupError("Onbekende herstelkeuze.")
    v = verified or verify_file(path, passphrase, collect=False)
    check_restorable(v.manifest)
    engine = create_async_engine(url or get_settings().database_url, poolclass=NullPool)
    report = RestoreReport()
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                await conn.run_sync(_restore_sync, path, passphrase, v.manifest, mode, report)
    except BackupError:
        raise
    except DBAPIError as exc:
        if "lock timeout" in str(exc):
            raise BackupError(
                "De database is bezet (er loopt nog een back-up of de worker is actief). Er is niets gewijzigd; "
                "probeer het over een paar minuten opnieuw."
            ) from exc
        raise BackupError(f"Herstel mislukt, er is niets gewijzigd: {getattr(exc, 'orig', exc)}") from exc
    finally:
        await engine.dispose()
    return report


def z2m_kept_secrets(manifest: dict | None, target: Path) -> list[str]:
    """Secret files a redacted backup carries without values, that `target` already has: restoring
    keeps those (with the real passwords) instead of overwriting them with blanks."""
    redacted = ((manifest or {}).get("zigbee2mqtt") or {}).get("redacted") or []
    return [r for r in redacted if (target / r).is_file()]


def extract_z2m(path: Path, passphrase: str, target: Path, manifest: dict | None = None) -> int:
    """Write the Zigbee2MQTT files from a (verified) backup into `target`; returns the file count.
    With the backup's ``manifest``: a secret file that a redacted backup has without values is not
    written over an existing one (``z2m_kept_secrets``), so Zigbee2MQTT keeps its MQTT password."""
    keep = set(z2m_kept_secrets(manifest, target))
    n = 0
    for name, f in iter_members(path, passphrase):
        if not name.startswith("zigbee2mqtt/"):
            continue
        p = target / name.removeprefix("zigbee2mqtt/")
        if ".." in p.relative_to(target).parts:
            raise BackupError(f"Onveilig pad in archief: {name}")
        if p.relative_to(target).as_posix() in keep:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as out:
            while block := f.read(1 << 20):
                out.write(block)
        n += 1
    return n
