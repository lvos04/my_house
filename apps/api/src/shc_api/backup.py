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
import hmac
import io
import json
import logging
import os
import stat
import struct
import tarfile
import tempfile
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import simplejson
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import Column, MetaData, Table, bindparam, case, func, literal, select, text
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from . import connector_transition
from .config import VERSION, get_settings
from .connector import RESTORED_COMMAND_DETAIL
from .connector_proof import (
    IN_FLIGHT,
    INCOMPLETE_PROOF,
    MEASURED_IDENTITY,
    MEASUREMENTS,
    OPEN_RECOVERY,
    PREVIEWS,
    PROPOSAL_SCOPE,
    REMEMBER_FIELDS,
    RESTORE_HISTORY,
    check_archive,
    check_preview,
)
from .logfilter import db_error_text
from .models import Base, Setting
from .store import ensure_server_identity

log = logging.getLogger(__name__)

MAGIC_V1 = b"SHCBAK1\n"
MAGIC = b"SHCBAK2\n"
MAGIC_LEN = len(MAGIC)
FORMAT = "shc-backup"
FORMAT_VERSION = 2  # Versie van de versleutelde stroom.
TYPED_ROWS_VERSION = 3  # Manifestversie. Oudere lezers weigeren de nieuwe rijcodering.
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
# Een applicatieback-up mag geen eerdere Hub-koppeling opnieuw vertrouwen geven.
CONNECTOR_LOCAL_TABLES = {"connector_hub_pins", "connector_nonces", "idempotency_keys"}
LOCAL_TABLES = {
    "sessions": "Browsersessies en CSRF-bewijzen zijn tijdelijk en gelden op één installatie.",
    "pairing_codes": "Een kortlopende eenmalige koppelcode geeft uitsluitend op de uitgevende installatie toegang.",
    "processed_events": "De worker gebruikt deze tijdelijke ontdubbelsleutels voor zijn eigen adapterverbinding.",
    "connector_hub_pins": "De beheerder brengt de vertrouwensrelatie met de Hub per installatie tot stand.",
    "connector_nonces": "De replaycache hoort bij de lokale Hub-koppeling.",
    "idempotency_keys": "De tijdelijke antwoordcache hoort bij de lokale executor en zijn verzoeken.",
}
SKIP_TABLES = LOCAL_TABLES.keys()


class BackupError(Exception):
    pass


class DatabaseDump(BackupError):
    """De versleutelde envelop bevat een afzonderlijk PostgreSQL-archief."""


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


def _json_integer(token: str):
    # PostgreSQL numeric kan meer cijfers bevatten dan de tekenlimiet van Python-int.
    return Decimal(token) if len(token) > 640 else int(token)


def _json_object(pairs):
    obj = dict(pairs)
    if len(obj) == len(pairs):
        return obj
    # PostgreSQL json (anders dan jsonb) bewaart ook eerdere waarden van een herhaalde veldnaam.
    members = (simplejson.dumps(k) + ":" + simplejson.dumps(v) for k, v in pairs)
    return simplejson.RawJSON("{" + ",".join(members) + "}")


def _load_json(raw):
    return simplejson.loads(raw, use_decimal=True, parse_int=_json_integer, object_pairs_hook=_json_object,
                            allow_nan=True)


class _ExactJSONText(sqltypes.TypeDecorator):
    impl = sqltypes.Text
    cache_ok = True

    def process_result_value(self, value, dialect):
        return _load_json(value) if value is not None else None


def _pg_text(col):
    # Niet-nullbare JSONB kan ieder JSON-document direct dragen. Bij een nullable JSONB
    # bewaart PostgreSQL-tekst het extra onderscheid: SQL NULL -> null, JSON null -> "null".
    if isinstance(col.type, JSONB):
        return col.nullable
    return not isinstance(col.type, (sqltypes.Integer, sqltypes.Boolean, sqltypes.String,
                                     sqltypes.Uuid, sqltypes.LargeBinary))


def _float_sender(col):
    if isinstance(col.type, sqltypes.Float):
        return func.float4send if isinstance(col.type, sqltypes.REAL) else func.float8send
    if isinstance(col.type, sqltypes.ARRAY) and isinstance(col.type.item_type, sqltypes.Float):
        return func.array_send
    return None


def archive_text(col):
    plain = col.cast(sqltypes.Text)
    sender = _float_sender(col)
    if sender is None:
        return plain
    # Alle NaN-bitpatronen krijgen dezelfde PostgreSQL-tekst. De overige floatwaarden
    # (en arrays met omkeerbare tekst) behouden de normale archiefnotatie.
    wire = sender(col)
    needs_binary = plain == "NaN" if isinstance(col.type, sqltypes.Float) else wire != sender(plain.cast(col.type))
    return case((needs_binary, literal("#") + func.encode(wire, "hex")), else_=plain)


def archive_types(columns, dialect):
    types = {}
    for c in columns:
        if isinstance(c.type, sqltypes.NullType):
            raise BackupError(f"Het gegevenstype van {c.table.name}.{c.name} kan niet worden vastgesteld.")
        types[c.name] = [c.type.compile(dialect=dialect), c.nullable]
    return types


# PostgreSQL leest zijn eigen tekstnotatie terug, ook voor oneindige datums, exacte numerics,
# json met herhaalde sleutels en arrays met eigen ondergrenzen of SQL NULL als element.
ARCHIVE_TEXT_SETTINGS = text("""SELECT set_config('TimeZone', 'UTC', true),
    set_config('DateStyle', 'ISO, YMD', true), set_config('IntervalStyle', 'iso_8601', true),
    set_config('extra_float_digits', '3', true), set_config('bytea_output', 'hex', true)""")


def archive_columns(columns):
    for col in columns:
        if _pg_text(col):
            yield archive_text(col).label(col.name)
        elif isinstance(col.type, JSONB):
            yield col.cast(_ExactJSONText).label(col.name)
        else:
            yield col


def archive_order(table, columns):
    return list(table.primary_key) or [
        func.row(*archive_columns(sorted(columns, key=lambda c: c.name))).cast(sqltypes.Text).collate("C")
    ]


_ENCODER = simplejson.JSONEncoder(separators=(",", ":"), default=_json_default, ensure_ascii=False,
                                 encoding=None, allow_nan=True)


def comparison_bytes(value) -> bytes:
    return simplejson.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":"),
                            encoding=None, allow_nan=True).encode()


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

    def __init__(self, raw, passphrase: str, created_at: str, chunk_size: int = CHUNK_SIZE, content: str | None = None,
                 *, dumpzegel: str | None = None):
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
        if dumpzegel is not None:
            header["dump_origin_v1"] = dumpzegel
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
                raise DatabaseDump(
                    "Dit bestand is een database-dump (pg_dump). Kies een nieuwe database in de herstelwizard "
                    "of gebruik 'shc-api restore-dump' (zie docs/onderhoud.md)."
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
        if "column_types" in info:
            tables[t]["column_types"] = info["column_types"]
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
        row = _load_json(line)
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
    source_id = None
    last_reading = None
    reading_ids_known = True
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
                if collect and table == "sensor_readings":
                    try:
                        reading_id = int(_load_json(line)["id"])
                    except (ValueError, KeyError, TypeError):
                        reading_ids_known = False
                    else:
                        last_reading = max(last_reading, reading_id) if last_reading is not None else reading_id
                if keep:
                    with contextlib.suppress(ValueError):
                        rows_by_table[table].append(_load_json(line))
                elif keep_secrets and (row := _secret_setting_row(line)) is not None:
                    secret_settings.append(row)
                elif keep_secrets and b'"server_id"' in line:
                    with contextlib.suppress(ValueError, TypeError, AttributeError, KeyError):
                        row = _load_json(line)
                        if row.get("key") == "server_id":
                            source_id = str(uuid.UUID(row["value"]["v"]))
        seen[name] = {"sha256": h.sha.hexdigest(), "size": h.size, "lines": lines}
    if manifest is None:
        raise BackupError("manifest.json ontbreekt.")
    manifest = _normalize_manifest(manifest)
    version = manifest.get("format_version")
    if (manifest.get("format") != FORMAT or type(version) is not int
            or version not in (1, FORMAT_VERSION, TYPED_ROWS_VERSION)):
        raise BackupError("Back-up is van een nieuwere of onbekende bestandsversie.")
    if meta and (meta.get("schema_revision") != manifest.get("schema_revision")
                 or ("format_version" in meta and meta["format_version"] != version)):
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
        summary["_server_id"] = source_id
        if proof := manifest.get("source_pause"):
            z2m = manifest.get("zigbee2mqtt") or {}
            if not isinstance(proof, dict):
                raise BackupError("Het pauzebewijs is ongeldig.")
            if (not z2m.get("mounted") or z2m.get("files") != 0 or z2m.get("unreadable") != []
                    or manifest.get("zigbee2mqtt_included")
                    or ("stopped_at" in proof and set(manifest["tables"]) != set(proof.get("inventory") or {}))
                    or not (set(Base.metadata.tables) - SKIP_TABLES).issubset(manifest["tables"])
                    or SKIP_TABLES & manifest["tables"].keys()):
                raise BackupError("De tabellen of de verwachte Zigbee-opslag ontbreken in de definitieve export.")
            if (not reading_ids_known or proof.get("last_reading") != last_reading
                    or proof.get("reading_count") != manifest["tables"].get("sensor_readings", {}).get("rows")
                    or proof.get("source_id") != source_id):
                raise BackupError("Het laatste bronrecord of de bronidentiteit wijkt af van het pauzebewijs.")
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


def key_check_warning(check: dict, *, web: bool = False) -> str | None:
    """Dutch restore warning for encrypted settings that do not fit this server's master key."""
    bad = [k for k in check.get("settings_failed") or [] if k in SECRET_SETTING_LABELS]
    if not bad:
        return None
    what = " en ".join(f"{_SECRET_SETTING_EFFECTS[k][0]} {SECRET_SETTING_LABELS[k]}" for k in bad)
    effects = " en ".join(_SECRET_SETTING_EFFECTS[k][1] for k in bad)
    again = " en ".join(_SECRET_SETTING_EFFECTS[k][2] for k in bad)
    fits = "past" if len(bad) == 1 else "passen"
    rekey = "de oude hoofdsleutel uploadt bij Herstellen en verhuizen" if web else (
        "de oude sleutel omzet met 'shc-api rekey --old-key-file'"
    )
    return (
        f"{what[0].upper()}{what[1:]} in deze back-up {fits} niet bij de hoofdsleutel van deze "
        f"server. Na het terugzetten {effects}, tot je {rekey} of {again} opnieuw instelt bij Systeem."
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
Z2M_CONFIG = "configuration.yaml"


def redacted_z2m_config(raw: bytes) -> tuple[bytes, bool]:
    """Exporteer alleen bekende gewone opties. Vrije YAML en kopieën kunnen netwerksleutels bevatten."""
    import yaml

    try:
        config = yaml.safe_load(raw)
    except (ValueError, yaml.YAMLError):
        config = None
    allowed = {
        "mqtt": {"password": ("!secret.yaml mqtt_password",), "user": ("zigbee2mqtt",),
                 "base_topic": ("zigbee2mqtt",)},
        "serial": {"port": ("/dev/zigbee",), "adapter": ("ember", "zstack", "deconz", "zigate", "zboss")},
        "frontend": {"enabled": (False,)}, "homeassistant": {"enabled": (False, True)},
        "availability": {"enabled": (False, True)},
    }
    clean = {}
    if isinstance(config, dict):
        if type(config.get("version")) is int and config["version"] in range(1, 6):
            clean["version"] = config["version"]
        for section, fields in allowed.items():
            source = config.get(section)
            if isinstance(source, dict):
                values = {k: v for k, options in fields.items() for v in options
                          if type(source.get(k)) is type(v) and source[k] == v}
                if values:
                    clean[section] = values
    return yaml.safe_dump(clean, sort_keys=False).encode(), clean != config


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
                    st = p.lstat()
                    if not stat.S_ISREG(st.st_mode) or st.st_size >= Z2M_MAX_FILE:
                        continue
                    if redact:
                        if rel.as_posix() == Z2M_CONFIG:
                            data, changed = redacted_z2m_config(p.read_bytes())
                        elif rel.as_posix() in Z2M_SECRET_FILES:
                            data, changed = redact_z2m_secrets(p.read_bytes()), True
                        else:
                            # Een coördinatorback-up of een willekeurige kopie kan dezelfde sleutel bevatten.
                            continue
                        name = f"zigbee2mqtt/{rel.as_posix()}"
                        _add_bytes(tar, name, data)
                        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                        report["files"] += 1
                        if changed:
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


@contextlib.asynccontextmanager
async def _backup_snapshot(conn, *, source_transaction):
    if source_transaction:
        # De bron sluit onder de exclusieve installatielock het aanmeldjournaal.
        # De export moet de rijen uit precies diezelfde, nog open transactie zien.
        yield conn
    else:
        await conn.rollback()
        snap = await conn.execution_options(isolation_level="REPEATABLE READ")
        async with snap.begin():
            await snap.execute(text("SET TRANSACTION READ ONLY"))
            yield snap


async def write_backup(
    conn: AsyncConnection,
    path: Path,
    passphrase: str,
    include_secrets: bool,
    z2m_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
    *, target_stamp: dict | None = None, source_proof: dict | None = None,
) -> dict:
    """Stream the whole database into an encrypted backup file at `path`; returns the manifest.

    Een gewone export gebruikt REPEATABLE READ. Een bronexport draait binnen de afgesloten schrijftransactie."""
    from .migration_inventory import table_key
    from .migration_inventory import tables as inventory_tables

    _check_passphrase(passphrase)
    created_at = dt.datetime.now(dt.UTC).isoformat()
    rev = await schema_revision(conn)
    meta = {
        "format": FORMAT,
        "format_version": TYPED_ROWS_VERSION,
        "app_version": VERSION,
        "connector_software": connector_transition.LOCAL_PROPOSAL,
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
            async with _backup_snapshot(conn, source_transaction=source_proof is not None) as snap:
                await snap.execute(ARCHIVE_TEXT_SETTINGS)
                # Een geredigeerde export gebruikt de bekende kolommen met hun bijbehorende
                # geheimenbeleid. Een volledige export leest alle werkelijke gegevenskolommen.
                export_tables = await snap.run_sync(inventory_tables)
                if not include_secrets:
                    export_tables = [t for t in export_tables if table_key(t) in Base.metadata.tables]
                restore_report = await snap.scalar(select(Setting.value["v"]).where(Setting.key == "restore_report"))
                interrupted = _interrupted_backup_ids(restore_report)
                if source_proof is not None:
                    from .cli import MigrationError
                    from .migration_inventory import capture
                    from .migration_source import (
                        PAUSE_KEY,
                        check_database_fence,
                        complete,
                        empty_zigbee_store,
                        fence_store,
                    )

                    pause = await snap.scalar(select(Setting.value["v"]).where(Setting.key == PAUSE_KEY))
                    try:
                        await snap.run_sync(check_database_fence)
                    except MigrationError as exc:
                        raise BackupError(str(exc)) from exc
                    z2m_dir = empty_zigbee_store()
                    fence_store(pause or {})
                    source_proof.update({k: v for k, v in (pause or {}).items()
                                         if k not in {"storage", "publishing", "file_id", "interim_file_id"}})
                    source_proof["zigbee_store"] = "write_fenced_empty"
                    source_proof["inventory_version"] = TYPED_ROWS_VERSION
                    source_proof["inventory"] = await snap.run_sync(capture, export_tables)
                    if not all(item["readable"] for item in source_proof["inventory"].values()):
                        raise BackupError("Controleer de oude hoofdsleutel op de bron vóór de definitieve export.")
                    counts = (await snap.execute(text("SELECT max(id), count(*) FROM sensor_readings"))).one()
                    if ((source_proof.get("stopped_at") and not complete(source_proof, source_proof.get("source_id")))
                            or counts != (source_proof.get("last_reading"), source_proof.get("reading_count"))):
                        raise BackupError("De schrijfpauze en het laatste bronrecord zijn niet bevestigd.")
                if target_stamp is not None:
                    from .restore_control import state_stamp
                    target_stamp["value"] = await snap.run_sync(state_stamp)
                for table in export_tables:
                    table_name = table_key(table)
                    columns = sorted((c for c in table.columns
                                      if include_secrets or c.name in Base.metadata.tables[table_name].columns),
                                     key=lambda c: c.name)
                    if progress:
                        progress(table_name)
                    pw = _PartWriter(tar, table_name, tmpdir)
                    stmt = select(*archive_columns(columns)).order_by(*archive_order(table, columns))
                    result = await snap.stream(stmt.execution_options(yield_per=FETCH_ROWS))
                    keys = list(result.keys())
                    redact = not include_secrets
                    async for partition in result.partitions(FETCH_ROWS):
                        for r in partition:
                            obj = dict(zip(keys, r, strict=True))
                            if table_name == "backups" and obj["status"] == "running" and obj["id"] not in interrupted:
                                continue  # Laat werkelijk lopende taken weg, inclusief deze export.
                            if table_name == "settings" and obj["key"] == "migration_source_pause":
                                continue  # Een lokale bronpauze wordt nooit op het doel geïnstalleerd.
                            if redact:
                                if table_name == "settings" and obj.get("key") in SECRET_SETTING_KEYS:
                                    continue
                                obj = redact_row(table_name, obj)
                            pw.add(_ENCODER.encode(obj).encode() + b"\n")
                    pw.flush(force=True)
                    for p in pw.parts:
                        files[p.name] = {"sha256": p.sha256, "size": p.size, "rows": p.rows}
                    tables[table_name] = {"rows": pw.rows, "files": [p.name for p in pw.parts],
                                          "column_types": archive_types(columns, snap.dialect)}
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
            if source_proof is not None:
                if z2m["files"] or z2m["unreadable"] or not z2m["mounted"]:
                    raise BackupError("De geverifieerde Zigbee2MQTT-opslag is gewijzigd.")
                # Een tussenexport levert ook aan oudere doelversies geen vrijgavebewijs.
                if source_proof.get("stopped_at"):
                    manifest["source_pause"] = source_proof
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
        cand = s.zigbee2mqtt_dir if s.zigbee2mqtt_dir is not None else s.data_dir / "zigbee2mqtt"
        z2m_dir = cand if cand.exists() else None
    engine = create_async_engine(url or s.database_url, poolclass=NullPool, hide_parameters=True)
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
            if isinstance(exc, BackupError):
                msg = str(exc)
            elif isinstance(exc, DBAPIError):
                msg = f"Back-up mislukt: {db_error_text(exc)}"
            else:
                msg = f"Back-up mislukt: {exc.__class__.__name__}: {exc}"
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


def _pg_dump_sync(url: str, target: Path, passphrase: str, created_at: str, *, snapshot: str | None = None) -> dict:
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
    connector_excludes = [f"--exclude-table-data=public.{table}" for table in sorted(CONNECTOR_LOCAL_TABLES)]
    source_snapshot = [f"--snapshot={snapshot}"] if snapshot else []
    # Een naamloos privébestand verdwijnt ook als het back-upproces stopt. De herkomst geldt voor
    # precies de door pg_dump geproduceerde bytes. De bestaande versleutelde bestandsindeling blijft leesbaar.
    with tempfile.TemporaryFile(dir=target.parent) as bron:
        with subprocess.Popen(  # noqa: S603 - vaste PostgreSQL-client
            [exe, "--format=custom", "--no-owner", "--no-privileges", *source_snapshot, *connector_excludes,
             f"--dbname={dsn}"], stdout=bron, stderr=subprocess.PIPE, env=env,
        ) as proc:
            _, err = proc.communicate()
            if proc.returncode:
                raise BackupError(f"pg_dump mislukt: {err.decode(errors='replace').strip()[:300] or proc.returncode}")
        plain = bron.tell()
        bron.seek(0)
        zegel = dumpzegel(hashlib.file_digest(bron, "sha256").digest())
        bron.seek(0)
        with open(target, "wb") as raw:
            enc = EncryptingWriter(raw, passphrase, created_at, content=DUMP_CONTENT, dumpzegel=zegel)
            while block := bron.read(CHUNK_SIZE):
                enc.write(block)
            enc.finish()
            raw.flush()
            os.fsync(raw.fileno())
    return {"file_sha256": enc.sha256.hexdigest(), "file_size": enc.size, "plain_bytes": plain}


def dumpzegel(vingerafdruk: bytes) -> str | None:
    try:
        sleutel = get_settings().resolved_secret_key()
    except (OSError, RuntimeError):
        return None
    if len(sleutel) < 32:
        return None
    return hmac.digest(sleutel.encode(), b"shc:pg-dump:origin:v1\0" + vingerafdruk, "sha256").hex()


def controleer_webdump(path: Path, passphrase: str, *, url: str | None = None) -> tuple[int, bool]:
    with tempfile.TemporaryDirectory(dir=path.parent) as work:
        return _lees_dump(path, passphrase, Path(work) / "controle.pgdump", url=url, vereis_herkomst=False)


def verify_dump(path: Path, passphrase: str, *, url: str | None = None) -> int:
    """Ontsleutel en controleer de opdrachtkoppelingen voordat de versleutelde dump wordt vrijgegeven."""
    return controleer_webdump(path, passphrase, url=url)[0]


def decrypt_dump(path: Path, passphrase: str, out: Path, *, url: str | None = None,
                 vereis_herkomst: bool = False) -> int:
    return _lees_dump(path, passphrase, out, url=url, vereis_herkomst=vereis_herkomst)[0]


def _lees_dump(path: Path, passphrase: str, out: Path, *, url: str | None, vereis_herkomst: bool) -> tuple[int, bool]:
    """Geef de ontsleutelde dump pas na de geïsoleerde terugzetproef atomair vrij."""
    import asyncpg

    from .cli import _in_thread

    _check_passphrase(passphrase)
    with tempfile.TemporaryDirectory(dir=out.parent) as work:
        tmp = Path(work) / "controle.pgdump"
        with path.open("rb") as source, tmp.open("xb") as dst:
            os.fchmod(dst.fileno(), 0o600)
            reader = DecryptingReader(source, passphrase)
            if reader.header.get("content") != DUMP_CONTENT:
                raise BackupError("Dit bestand is geen database-dump; gebruik 'shc-api restore' voor een export.")
            stream = io.BufferedReader(reader, buffer_size=CHUNK_SIZE)
            if stream.read(len(PGDMP_MAGIC)) != PGDMP_MAGIC:
                raise BackupError("Database-dump beschadigd (geen pg_dump-archief).")
            vingerafdruk = hashlib.sha256(PGDMP_MAGIC)
            dst.write(PGDMP_MAGIC)
            while block := stream.read(CHUNK_SIZE):
                dst.write(block)
                vingerafdruk.update(block)
            size = dst.tell()
            verwacht = dumpzegel(vingerafdruk.digest())
            gevonden = reader.header.get("dump_origin_v1")
            lokaal = (verwacht is not None and isinstance(gevonden, str) and gevonden.isascii()
                      and hmac.compare_digest(gevonden, verwacht))
            if vereis_herkomst and not lokaal:
                raise BackupError("De herkomst van de dump is niet bevestigd met de installatiesleutel.")
        try:
            _in_thread(lambda: asyncio.run(check_archive(tmp, url or get_settings().database_url)))
        except (OSError, ValueError, IndexError, asyncpg.PostgresError) as exc:
            raise BackupError("De terugzetproef van het connectorherstelbewijs is mislukt.") from exc
        tmp.replace(out)
        return size, lokaal


async def _connector_dump_snapshot(source) -> str:
    # De export sluit lokale rechten uit. Controleer de rechten in dezelfde momentopname als
    # pg_dump, ook als de oorspronkelijke bewijsrij bij schematerugval tijdelijk in settings zit.
    await source.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    if await source.scalar(text("SELECT to_regclass('idempotency_keys') IS NOT NULL")):
        live = ""
        if await source.scalar(text("SELECT to_regclass('connector_changes') IS NOT NULL")):
            live = "UNION ALL SELECT id::text, command_id::text, rollback_command_id::text FROM connector_changes"
        if await source.scalar(text(f"""
            WITH proof(id, command_id, undo_id) AS (
                SELECT value->'v'->>'id', value->'v'->>'command_id', value->'v'->>'rollback_command_id' FROM settings
                WHERE value->>'format' = 'connector-0007' AND key = 'connector_history:' || (value->'v'->>'id')
                {live}
            )
            SELECT EXISTS (SELECT 1 FROM proof p JOIN commands cmd ON cmd.id::text IN (p.command_id, p.undo_id)
                           JOIN idempotency_keys k ON k.key::text = p.id
                           WHERE cmd.status='cancelled' AND {PROPOSAL_SCOPE})
        """)):  # noqa: S608 - vaste tabellen en bewijsvoorwaarden
            raise BackupError("Het connectorherstelbewijs van de schema-annulering bevat nog uitvoerrecht.")
    return await source.scalar(text("SELECT pg_export_snapshot()"))


async def run_db_dump(
    passphrase: str,
    *,
    auto: bool = True,
    url: str | None = None,
    backup_dir: Path | None = None,
) -> dict:
    """Make an encrypted database dump + its ``backups`` row (kind "db_dump"). Lighter than a full
    export (no Zigbee2MQTT files, restored to a separate database). Verified after writing."""
    from .models import BackupRecord

    _check_passphrase(passphrase)
    s = get_settings()
    db_url = url or s.database_url
    backup_dir = backup_dir or s.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(db_url, poolclass=NullPool, hide_parameters=True)
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
            async with engine.begin() as source:
                snapshot = await _connector_dump_snapshot(source)
                info = await asyncio.to_thread(_pg_dump_sync, db_url, tmp, passphrase, created_at, snapshot=snapshot)
            await asyncio.to_thread(verify_dump, tmp, passphrase, url=db_url)
            tmp.replace(path)
        except BaseException as exc:
            tmp.unlink(missing_ok=True)
            if isinstance(exc, BackupError):
                msg = str(exc)
            elif isinstance(exc, DBAPIError):
                msg = f"Database-dump mislukt: {db_error_text(exc)}"
            else:
                msg = f"Database-dump mislukt: {exc.__class__.__name__}: {exc}"
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
                "restore": "Volg docs/onderhoud.md, ‘Database-dump terugzetten’, voor herstel en Hub-herbevestiging.",
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
    interrupted_backups: list[str] = field(default_factory=list)
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
            "interrupted_backups": self.interrupted_backups,
            "drafts": self.drafts,
        }


async def restored_interrupted_backup_ids(db: AsyncSession) -> set[uuid.UUID]:
    from .store import get_setting

    return _interrupted_backup_ids(await get_setting(db, "restore_report"))


def _interrupted_backup_ids(report: Any) -> set[uuid.UUID]:
    if not isinstance(report, dict) or not isinstance(report.get("interrupted_backups"), list):
        return set()
    ids = set()
    for value in report["interrupted_backups"]:
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                ids.add(uuid.UUID(value))
    return ids


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


def _hold_connector_recovery(conn) -> None:
    # De bestaande open herstelstand kan actueler zijn dan de back-up.
    conn.execute(text(f"""
        CREATE TEMP TABLE held_connector_recovery ON COMMIT DROP AS
        SELECT id, "before", after, rollback_from, rollback_after, command_id, rollback_command_id,
               status, reason_code, rollback_status, rollback_reason_code, {IN_FLIGHT} AS command_uncertain,
               {MEASURED_IDENTITY} AS measurement
        FROM connector_changes c {MEASUREMENTS} WHERE {OPEN_RECOVERY}
    """))  # noqa: S608 - beide SQL-fragmenten zijn vaste broncode


def _restore_connector_proof(conn, held_recovery: bool, *, version_changed: bool) -> None:
    conn.execute(text(RESTORE_HISTORY))
    if held_recovery and conn.execute(text(f"""
        SELECT EXISTS (
            SELECT * FROM held_connector_recovery
            EXCEPT
            SELECT id, "before", after, rollback_from, rollback_after, command_id, rollback_command_id,
                   status, reason_code, rollback_status, rollback_reason_code, {IN_FLIGHT}, {MEASURED_IDENTITY}
            FROM connector_changes c {MEASUREMENTS}
        )
    """)).scalar_one():  # noqa: S608 - IN_FLIGHT is vaste broncode
        raise BackupError("Terugzetten geweigerd: het bestaande open apparaatherstel zou bewijs verliezen.")
    if conn.scalar(text(INCOMPLETE_PROOF)):
        raise BackupError("Terugzetten geweigerd: bij een connectoropdracht ontbreekt het herstelbewijs.")
    conn.execute(text(REMEMBER_FIELDS))
    for raw in conn.execute(text(PREVIEWS)).scalars():
        check_preview(raw)
    conn.execute(text("""
        UPDATE connector_changes SET status = 'failed', reason_code = 'readback_unknown_after_restore'
        WHERE status = 'queued' AND command_id IN (SELECT id FROM commands WHERE status IN ('sending', 'sent'))
    """))
    conn.execute(text("""
        UPDATE connector_changes SET rollback_status = 'failed', rollback_reason_code = 'readback_unknown_after_restore'
        WHERE rollback_status = 'queued'
          AND rollback_command_id IN (SELECT id FROM commands WHERE status IN ('sending', 'sent'))
    """))
    conn.execute(text("UPDATE connector_changes SET execution_expires_at = NULL, rollback_expires_at = NULL"))
    if version_changed:
        conn.execute(text(connector_transition.CANCEL_PENDING))
    conn.execute(text("""
        UPDATE commands SET status = 'cancelled', updated_at = now(), detail = :detail
        WHERE source = 'connector' AND status IN ('queued', 'sending', 'sent')
    """), {"detail": RESTORED_COMMAND_DETAIL})


@dataclass(frozen=True)
class MigrationTarget:
    server_id: str
    admin_id: uuid.UUID
    source_id: str


def check_migration_target(conn, tables, target: MigrationTarget) -> None:
    try:
        uuid.UUID(target.server_id)
    except (ValueError, AttributeError) as exc:
        raise BackupError("De doelinstallatie is niet herkenbaar.") from exc
    if target.server_id == target.source_id:
        raise BackupError("De back-up komt van deze installatie. Kies een afzonderlijke doelinstallatie.")
    settings = dict(conn.execute(text("SELECT key, value->'v' FROM settings")).all())
    if settings.get("server_id") != target.server_id:
        raise BackupError("De doelinstallatie is gewijzigd of niet herkenbaar.")
    admins = conn.execute(text("SELECT id FROM users WHERE role = 'admin' AND NOT disabled")).scalars().all()
    if admins != [target.admin_id] or conn.scalar(text("SELECT count(*) FROM users")) != 1:
        raise BackupError("Het doel moet leeg zijn, met alleen het tijdelijke beheerdersaccount.")
    runtime = {
        "server_id", "commissioning_gate", "worker_heartbeat", "worker_loops", "clock_check",
        "maintenance", "safe_mode", "status_watch",
    }
    if settings.keys() - runtime:
        raise BackupError("Het doel bevat al instellingen. Gebruik een nieuwe installatie met eigen opslag.")
    gate = settings.get("commissioning_gate") or {}
    if gate.get("origin") != "fresh" or gate.get("enabled") is not True or gate.get("enabled_by") != "systeem":
        raise BackupError("Het doel bevat een gewijzigde inbedrijfstelling.")
    # Alleen het aanmaken van de tijdelijke beheerder en de eigen herstelwizard horen bij de inrichting.
    # Een login, gelezen melding of eerdere beheerhandeling is geschiedenis op het doel.
    if conn.scalar(text("""
        SELECT (SELECT count(*) FROM audit_log WHERE action = 'setup_admin') <> 1
        OR EXISTS (SELECT FROM audit_log a WHERE (
            a.actor = (SELECT username FROM users WHERE id = :admin) AND (
                (a.action = 'setup_admin' AND a.target IS NULL) OR
                (a.action IN ('restore_verified', 'restore_drill') AND a.target ~ '^[0-9a-f]{32}$')
            )) IS NOT TRUE)
        """), {"admin": target.admin_id}):
        raise BackupError("Het doel bevat al gebruikershistorie in het auditlog.")
    bootstrap = {"alembic_version", "settings", "users", "sessions", "audit_log"}
    if any(conn.scalar(select(table.select().exists())) for name, table in tables.items() if name not in bootstrap):
        raise BackupError("Het doel bevat al gegevens. Gebruik een nieuwe installatie met eigen opslag.")


def _restore_sync(
    conn, path: Path, passphrase: str, manifest: dict, mode: str, report: RestoreReport,
    migration: MigrationTarget | None = None, guard: dict | None = None, job_key: str | None = None,
) -> dict:
    from alembic import command

    from .commissioning import KEY, LOCK_SQL, gate_state, restored_gate
    from .migration_inventory import catalog, compared_columns, fingerprint, table_key

    conn.execute(text("SET LOCAL lock_timeout = '60s'"))
    conn.execute(text(LOCK_SQL))
    typed = manifest["format_version"] == TYPED_ROWS_VERSION
    if typed:
        conn.execute(ARCHIVE_TEXT_SETTINGS)
    rev = manifest["schema_revision"]
    head = head_revision()
    report.schema_from, report.schema_to = rev, head
    current = catalog(conn)
    quote = conn.dialect.identifier_preparer.format_table
    if migration or guard:
        # Controle en TRUNCATE zitten onder dezelfde tabelsloten. Een gelijktijdige schrijfactie
        # kan het doel niet tussen de twee stappen vullen.
        names = ", ".join(quote(t) for t in current.values())
        conn.execute(text(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE"))
        if migration:
            check_migration_target(conn, current, migration)
            from .migration_source import TYPED_PROOF_REQUIRED, complete
            if not typed or (manifest.get("source_pause") or {}).get("inventory_version") != TYPED_ROWS_VERSION:
                raise BackupError(TYPED_PROOF_REQUIRED)
            if not complete(manifest.get("source_pause"), migration.source_id):
                raise BackupError("De afgesloten bronregistratie is niet aangetoond of is verlopen.")
        if guard:
            from .restore_control import check_guard
            pending = conn.scalar(text("SELECT value->'v' FROM settings WHERE key='migration_pending'"))
            if pending and (not isinstance(pending, dict)
                            or pending.get("rollback_sha256") != guard["comparison"]["archive_sha256"]):
                raise BackupError("Bij een open overstap kun je uitsluitend de geverifieerde doelback-up terugzetten.")
            check_guard(conn, guard, path)
    db_rev = None
    if "alembic_version" in current:
        db_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    held_recovery = "connector_changes" in current
    if held_recovery:
        conn.execute(text(RESTORE_HISTORY))
        _hold_connector_recovery(conn)
    prior_raw = None
    if "settings" in current:
        prior_raw = conn.execute(text("SELECT value FROM settings WHERE key=:key"), {"key": KEY}).scalar()
    # Alleen vóór invoering mag ontbreken nog de eenmalige classificatie volgen.
    introduced = db_rev is not None and (db_rev not in known_revisions() or not _is_older(db_rev, "0005"))
    prior = gate_state(prior_raw.get("v") if isinstance(prior_raw, dict) else None)
    if prior_raw is None and not introduced:
        prior = None
    # Installatiegebonden taakjournalen blijven buiten de vervanging. Een oudere kopie zonder
    # een aanvullend schema mag gegevens uit dat schema evenmin impliciet wissen.
    names = [quote(t) for key, t in current.items() if key != "alembic_version"
             and (key in Base.metadata.tables or key in manifest["tables"])]
    if names:
        conn.execute(text("TRUNCATE " + ", ".join(names) + " RESTART IDENTITY"))
    cfg = alembic_config(connection=conn)
    if db_rev != rev:
        # Load the rows into the schema they were written with; migrate forward afterwards.
        if _is_older(rev, db_rev):
            command.downgrade(cfg, rev)
        else:
            command.upgrade(cfg, rev)
        report.migrated = True
    # Schemaopbouw in een lege database kan deze ene initialisatierij maken.
    # De inhoud van de back-up wordt hierna geladen; geen dubbele gate-rij.
    conn.execute(text("DELETE FROM settings WHERE key=:key"), {"key": KEY})
    target = catalog(conn)
    batch: list[dict] = []
    table = None
    skipped_cols: set[str] = set()
    # Een import legt bestaande plaatsingen en hun oorspronkelijke versies terug.
    conn.execute(text("SET LOCAL shc.restoring_placements = 'on'"))
    for name, f in iter_members(path, passphrase):
        tname = _table_of(name)
        if tname is None:
            continue
        if tname not in target or tname == "alembic_version" or tname in CONNECTOR_LOCAL_TABLES:
            n = sum(1 for line in _lines(f) if line)
            report.dropped["tables"][tname] = report.dropped["tables"].get(tname, 0) + n
            continue
        if table is None or table_key(table) != tname:
            _flush(conn, table, batch, report, typed=typed)
            batch = []
            table = target[tname]
            skipped_cols = set()
            if typed:
                source_types = manifest["tables"].get(tname, {}).get("column_types")
                target_types = archive_types(table.columns, conn.dialect)
                if not isinstance(source_types, dict) or not source_types or any(
                    source_types[k] != target_types[k] for k in source_types.keys() & target_types.keys()
                ):
                    raise BackupError(f"De kolomtypen voor {tname} ontbreken of wijken af van het doel.")
        cols = table.columns
        for line in _lines(f):
            if not line:
                continue
            obj = _load_json(line)
            if typed and (not isinstance(obj, dict) or obj.keys() != source_types.keys()):
                raise BackupError(f"Een rij van {tname} bevat niet precies de vastgelegde kolommen.")
            if tname == "settings" and obj.get("key") == "migration_source_pause":
                continue  # Een bronpauze en de bijbehorende triggers zijn lokaal.
            row = {}
            for k, v in obj.items():
                c = cols.get(k)
                if c is None:
                    if k not in skipped_cols:
                        skipped_cols.add(k)
                        report.dropped["columns"].setdefault(tname, []).append(k)
                    continue
                if typed and _pg_text(c):
                    if v is not None and not isinstance(v, str):
                        raise BackupError(f"Een waarde in {tname}.{k} mist de vastgelegde tekstnotatie.")
                    row[k] = v
                else:
                    row[k] = _dec(c, v)
            batch.append(row)
            if len(batch) >= INSERT_BATCH:
                _flush(conn, table, batch, report, typed=typed)
                batch = []
    _flush(conn, table, batch, report, typed=typed)
    conn.execute(text("SET LOCAL shc.restoring_placements = 'off'"))
    final = target
    if rev != head:
        command.upgrade(cfg, "head")
        report.migrated = True
        final = catalog(conn)
        for t, n in report.counts.items():
            if t in final:
                now = conn.execute(select(func.count()).select_from(final[t])).scalar_one()
                if now < n:
                    report.dropped["rows_removed_by_migration"][t] = n - now
    if migration:
        inventory = (manifest.get("source_pause") or {}).get("inventory", {})
        columns = {name: sorted(c.name for c in compared_columns(t)) for name, t in final.items()
                   if name != "alembic_version" and name not in SKIP_TABLES}
        if any(report.dropped.values()) or {name: item.get("columns") for name, item in inventory.items()} != columns:
            raise BackupError("De bronvergelijking dekt niet alle tabellen, kolommen of rijen op het doel.")
        for name in ("commands", "connector_changes"):
            if inventory[name] != fingerprint(conn, target[name]):
                raise BackupError("De geïmporteerde opdrachten of het connectorbewijs wijken af van de bron.")
    # Ook twee builds met hetzelfde versienummer kunnen een andere worker hebben.
    # Archieven van vóór deze bouwidentiteit komen uit eerdere software.
    version_changed = (rev != head or manifest.get("app_version") != VERSION
                       or manifest.get("connector_software") != connector_transition.LOCAL_PROPOSAL)
    _restore_connector_proof(conn, held_recovery, version_changed=version_changed)
    for name, table in final.items():
        pk = list(table.primary_key)
        if name in report.counts and len(pk) == 1 and isinstance(pk[0].type, sqltypes.Integer):
            sequence = conn.scalar(text("SELECT pg_get_serial_sequence(:rel, :col)"),
                                   {"rel": quote(table), "col": pk[0].name})
            if sequence:
                last = conn.scalar(select(func.max(pk[0])))
                conn.execute(text("SELECT setval(CAST(:seq AS regclass), :value, :used)"),
                             {"seq": sequence, "value": last if last is not None else 1, "used": last is not None})
    loaded_raw = conn.execute(text("SELECT value FROM settings WHERE key='commissioning_gate'")).scalar()
    loaded = gate_state(loaded_raw.get("v") if isinstance(loaded_raw, dict) else None)
    merged = restored_gate(prior, loaded, mode, dt.datetime.now(dt.UTC).isoformat())
    _set(conn, KEY, merged)
    # Een back-up op het huidige schema slaat de datamigratie over. Repareer ongedateerde koppelingen ook hier.
    conn.execute(text(
        "UPDATE api_clients SET expires_at = CASE "
        "WHEN isfinite(created_at) THEN LEAST(created_at, now()) + interval '4320 hours' "
        "ELSE now() END WHERE expires_at IS NULL"
    ))
    ensure_server_identity(conn)
    _after_restore(conn, manifest, mode, report)
    if migration:
        _set(conn, "server_id", migration.server_id)
        conn.execute(text("""
            UPDATE commands SET status = 'cancelled', detail = 'Vervallen bij overstap.'
            WHERE status IN ('queued', 'sending', 'sent')
        """))
        pause = {key: value for key, value in manifest["source_pause"].items() if key != "inventory"}
        inventory = dict(inventory)
        # Opdrachttermijnen vervallen en de strengste inbedrijfstellingsgate blijft behouden.
        # Leg uitsluitend deze bedoelde omzettingen vast, binnen de transactie van het herstel.
        for name in ("commands", "connector_changes"):
            inventory[name] = fingerprint(conn, target[name])
        _set(conn, "migration_pending", {"source_id": migration.source_id, "target_id": migration.server_id,
                                         "source_pause": pause, "inventory": inventory, "gate": merged,
                                         "archive_sha256": guard["comparison"]["archive_sha256"] if guard else None,
                                         "rollback_sha256": guard["comparison"]["rollback_sha256"] if guard else None})

    if job_key:
        from .restore_control import finish_job
        finish_job(conn, job_key, state="done", step="teruggezet", report=report.as_dict())
    return merged


def _is_older(rev: str, than: str | None) -> bool:
    revs = known_revisions()
    return than is not None and than in revs and revs.index(rev) < revs.index(than)


def _typed_bind(conn, col, batch, temporary):
    param = bindparam(col.name, type_=sqltypes.Text)
    plain = param.cast(col.type)
    if _float_sender(col) is None:
        return plain
    codes = {r[col.name] for r in batch if r[col.name] is not None and r[col.name].startswith("#")}
    if not codes:
        return plain
    lookup = Table(f"shc_float_{uuid.uuid4().hex}", MetaData(),
                   Column("code", sqltypes.Text, primary_key=True), Column("waarde", col.type),
                   prefixes=["TEMPORARY"], postgresql_on_commit="DROP")
    lookup.create(conn)
    temporary.append(lookup)

    async def load(driver):
        # COPY van bytea levert de oorspronkelijke veldbytes. COPY naar het werkelijke
        # kolomtype laat PostgreSQL diezelfde bytes in deze hersteltransactie inlezen.
        stream = io.BytesIO()
        await driver.copy_from_query("SELECT v, decode(substr(v, 2), 'hex') FROM unnest($1::text[]) AS v",
                                     list(codes), output=stream, format="binary")
        stream.seek(0)
        await driver.copy_to_table(lookup.name, schema_name="pg_temp", source=stream, format="binary")

    try:
        conn.connection.dbapi_connection.run_async(load)
    except asyncpg.PostgresError as exc:
        raise BackupError(f"Ongeldige binaire waarde in {col.table.name}.{col.name}.") from exc
    exact = select(lookup.c.waarde).where(lookup.c.code == param).scalar_subquery()
    return case((param.startswith("#"), exact), else_=plain)


def _flush(conn, table, batch: list[dict], report: RestoreReport, *, typed=False) -> None:
    if table is None:
        return
    from .migration_inventory import table_key
    key = table_key(table)
    report.counts.setdefault(key, 0)
    if not batch:
        return
    insert = table.insert()
    temporary = []
    if typed:
        insert = insert.values({c: _typed_bind(conn, c, batch, temporary) for c in table.columns
                                if c.name in batch[0] and _pg_text(c)})
    elif key == "connector_changes":
        # De oude schrijver legde een ontbrekende teruglezing als SQL NULL vast.
        # Schema-reflectie verliest de schrijfregel none_as_null van deze bewijsvelden.
        for col in Base.metadata.tables[key].columns:
            if col.name in batch[0] and isinstance(col.type, JSONB) and col.type.none_as_null:
                insert = insert.values({col.name: bindparam(col.name, type_=col.type)})
    conn.execute(insert, batch)
    for lookup in temporary:
        lookup.drop(conn)
    report.counts[key] += len(batch)


def _set(conn, key: str, value: Any) -> None:
    conn.execute(
        text(
            "INSERT INTO settings (key, value) VALUES (:k, CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": key, "v": simplejson.dumps({"v": value}, default=str)},
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
                "s": simplejson.dumps(p.schedule or []),
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
                    "d": simplejson.dumps(b.days),
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
            {"p": z.profile, "pid": profile_map.get(z.profile_id), "pol": simplejson.dumps(policy), "id": target},
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
    source_homes = {str(h.id): h.name for h in conn.execute(text("SELECT id, name FROM homes ORDER BY id"))}
    devices = conn.execute(
        text("SELECT id, provider, kind, name, stable_identity, capabilities, state, online, home_id, lifecycle "
             "FROM devices")
    ).all()
    snapshot = {
        str(d.id): {
            "provider": d.provider,
            "kind": d.kind,
            "name": d.name,
            "stable_identity": d.stable_identity,
            "capabilities": d.capabilities or [],
            "caps": (d.state or {}).get("caps"),
            "home_id": str(d.home_id), "lifecycle": d.lifecycle,
        }
        for d in devices
    }
    functions = [dict(row) for row in conn.execute(text("""
        SELECT 'role:' || r.key AS key, r.label, r.home_id::text, b.device_id::text,
               b.id::text AS binding_id, r.id::text AS function_id
        FROM logical_roles r JOIN device_bindings b ON b.role_id = r.id
        WHERE b.effective_to IS NULL
        UNION ALL
        SELECT 'zone:' || z.id || ':' || p.part, z.name || ' (' || p.part || ')', z.home_id::text,
               p.device_id::text, NULL, z.id::text
        FROM climate_zones z CROSS JOIN LATERAL
        (VALUES ('airco', z.unit_device_id), ('sensor', z.sensor_device_id)) AS p(part, device_id)
        WHERE p.device_id IS NOT NULL
        UNION ALL
        SELECT 'lights:' || id, 'Licht ' || name, home_id::text, id::text, NULL, id::text
        FROM devices WHERE kind = 'light_group'
    """)).mappings()]
    definitions = [dict(row) for row in conn.execute(text("""
        SELECT id::text, home_id::text, 'role:' || key AS key, label FROM logical_roles
        UNION ALL SELECT id::text, home_id::text, 'zone:' || id, name FROM climate_zones
    """)).mappings()]
    buttons = list(conn.scalars(text("SELECT to_jsonb(b) FROM button_mappings b ORDER BY id")))
    active_homes = [str(h) for h in conn.execute(text("SELECT id FROM homes WHERE active_to IS NULL")).scalars()]
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
    report.interrupted_backups = [str(backup_id) for backup_id in conn.execute(
        text("SELECT id FROM backups WHERE status = 'running' ORDER BY id"),
    ).scalars()]
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
    _set(conn, "restore_snapshot", {"restored_at": now.isoformat(), "devices": snapshot, "homes": source_homes,
                                    "mode": mode, "active_homes": active_homes, "functions": functions,
                                    "definitions": definitions, "buttons": buttons})
    _set(conn, "restore_report", report.as_dict())


async def restore_file(
    path: Path,
    passphrase: str,
    *,
    url: str | None = None,
    mode: str = "same_home",
    verified: Verified | None = None,
    migration: MigrationTarget | None = None,
    guard: dict | None = None, job_key: str | None = None,
) -> RestoreReport:
    """Verify (pass 1) and restore (pass 2) in one transaction. Caller handles maintenance mode."""
    from .connector_changes import remember_proposal_fields

    if mode not in ("same_home", "new_home", "migration") or (mode == "migration") != (migration is not None):
        raise BackupError("Onbekende herstelkeuze.")
    v = verified or verify_file(path, passphrase, collect=migration is not None)
    check_restorable(v.manifest)
    if migration and (v.summary.get("_server_id") != migration.source_id or not v.manifest.get("secrets_included")
                      or v.manifest.get("redacted")):
        raise BackupError("De herkomst of volledigheid van de back-up wijkt af van de herstelproef.")
    engine = create_async_engine(url or get_settings().database_url, poolclass=NullPool, hide_parameters=True,
                                 json_serializer=simplejson.dumps, json_deserializer=_load_json)
    report = RestoreReport()
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                gate = await conn.run_sync(_restore_sync, path, passphrase, v.manifest, mode, report,
                                           migration, guard, job_key)
                await remember_proposal_fields(conn)
    except BackupError:
        raise
    except DBAPIError as exc:
        if "lock timeout" in str(exc):
            raise BackupError(
                "De database is bezet (er loopt nog een back-up of de worker is actief). Er is niets gewijzigd; "
                "probeer het over een paar minuten opnieuw."
            ) from exc
        raise BackupError(f"Herstel mislukt, er is niets gewijzigd: {db_error_text(exc)}") from exc
    finally:
        await engine.dispose()
    log.info(
        "Inbedrijfstellingsgate na terugzetten: aan=%s; eerdere toestand behouden=%s.",
        gate["enabled"], gate["restored"]["source"] == "before",
    )
    return report


def z2m_kept_secrets(manifest: dict | None, target: Path) -> list[str]:
    """Secret files a redacted backup carries without values, that `target` already has: restoring
    keeps those (with the real passwords) instead of overwriting them with blanks."""
    if not manifest or not manifest.get("redacted"):
        return []
    return sorted(name for name in Z2M_SECRET_FILES | {Z2M_CONFIG}
                  if f"zigbee2mqtt/{name}" in manifest["files"] and (target / name).is_file())


def extract_z2m(path: Path, passphrase: str, target: Path, manifest: dict | None = None,
                *, new_files_only: bool = False) -> int:
    """Write the Zigbee2MQTT files from a (verified) backup into `target`; returns the file count.
    With the backup's ``manifest``: a secret file that a redacted backup has without values is not
    written over an existing one (``z2m_kept_secrets``), so Zigbee2MQTT keeps its MQTT password."""
    if manifest is None:
        manifest = verify_file(path, passphrase, collect=False).manifest
    keep = set(z2m_kept_secrets(manifest, target))
    n = 0
    for name, f in iter_members(path, passphrase):
        if not name.startswith("zigbee2mqtt/"):
            continue
        p = target / name.removeprefix("zigbee2mqtt/")
        if ".." in p.relative_to(target).parts:
            raise BackupError(f"Onveilig pad in archief: {name}")
        relative = p.relative_to(target).as_posix()
        redacted = manifest.get("redacted") is True
        if relative in keep or (redacted and relative not in Z2M_SECRET_FILES | {Z2M_CONFIG}):
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        # Publiceer pas na de volledige kopie. Een inmiddels aanwezige sleutel blijft bij redactie behouden.
        with tempfile.TemporaryDirectory(dir=p.parent) as work:
            ready = Path(work) / "herstel"
            with ready.open("xb") as out:
                os.fchmod(out.fileno(), 0o600)
                if redacted and relative == Z2M_CONFIG:
                    out.write(redacted_z2m_config(f.read())[0])
                else:
                    while block := f.read(1 << 20):
                        out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if new_files_only:
                os.link(ready, p)
            elif redacted:
                try:
                    os.link(ready, p)
                except FileExistsError:
                    continue
            else:
                os.replace(ready, p)
        n += 1
    return n
