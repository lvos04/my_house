from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from collections import OrderedDict, deque
from typing import Any
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_hasher = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile)

MIN_PASSWORD_LENGTH = 12
TOKEN_PREFIX = "shc_"  # noqa: S105 (prefix, not a secret)
# Unambiguous alphabet for codes typed by hand.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


# Constant-ish time dummy verify so unknown usernames cost the same as wrong passwords.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def dummy_verify() -> None:
    verify_password(_DUMMY_HASH, "x")


# --- Argon2 off the event loop (verbeterlijst 16) ------------------------------------------------
#
# One Argon2 call costs ~50 ms of CPU. On the event loop that freezes the whole API (web, app, live
# updates, healthcheck) under a burst of login attempts. So hashing runs in a worker thread, at most
# MAX_CONCURRENT_HASHES at a time; when both slots are busy the caller answers 429 right away instead
# of queueing (a queue would only move the overload into memory and latency).

MAX_CONCURRENT_HASHES = 2
_inflight = 0


class HashBusy(Exception):
    """All password-hashing slots are in use; answer 429 and let the client retry."""


async def _gated(fn, *args):
    global _inflight
    if _inflight >= MAX_CONCURRENT_HASHES:
        raise HashBusy
    _inflight += 1  # single-threaded event loop: check-and-increment cannot race
    try:
        return await asyncio.to_thread(fn, *args)
    finally:
        _inflight -= 1


async def verify_password_async(stored: str, password: str) -> bool:
    return await _gated(verify_password, stored, password)


async def hash_password_async(password: str) -> str:
    return await _gated(hash_password, password)


async def dummy_verify_async() -> None:
    await _gated(dummy_verify)


# --- attempt limiting --------------------------------------------------------------------------


class AttemptLimiter:
    """Failed attempts per key (client IP) in a sliding window, in memory.

    Bounded: at most ``max_keys`` keys are kept; the least recently used key is dropped first, so a
    flood of different (spoofed or real) addresses cannot grow memory without limit."""

    def __init__(self, max_attempts: int, window_s: float, max_keys: int = 10_000) -> None:
        self.max_attempts = max_attempts
        self.window_s = window_s
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def _prune(self, key: str, now: float) -> deque[float] | None:
        q = self._hits.get(key)
        if q is None:
            return None
        while q and now - q[0] > self.window_s:
            q.popleft()
        if not q:
            del self._hits[key]
            return None
        return q

    def blocked(self, key: str) -> bool:
        q = self._prune(key, time.monotonic())
        return q is not None and len(q) >= self.max_attempts

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest counted attempt leaves the window (0 when not blocked)."""
        now = time.monotonic()
        q = self._prune(key, now)
        if q is None or len(q) < self.max_attempts:
            return 0
        return max(1, int(self.window_s - (now - q[0])) + 1)

    def hit(self, key: str) -> None:
        now = time.monotonic()
        q = self._prune(key, now)
        if q is None:
            q = self._hits[key] = deque()
        else:
            self._hits.move_to_end(key)
        q.append(now)
        if len(q) > self.max_attempts:  # older entries add nothing once the limit is reached
            q.popleft()
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def clear(self) -> None:
        self._hits.clear()

    def __len__(self) -> int:
        return len(self._hits)


def lockout_seconds(failures: int, threshold: int, base_s: int = 60, max_s: int = 900) -> int:
    """Per-username lockout after ``threshold`` consecutive failures: base_s, doubling with every
    further failure, capped at max_s (60 s, 2, 4, 8 min, then 15 min)."""
    if threshold <= 0 or failures < threshold:
        return 0
    return min(max_s, base_s * 2 ** min(failures - threshold, 16))


# --- server addresses handed to phones (verbeterlijst 17) --------------------------------------

LOCAL_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain")


def is_private_host(host: str) -> bool:
    """A host on the home network: private/link-local IP, or a .local-style or single-label name.
    Loopback is not: a phone cannot reach the server there."""
    h = host.lower().strip("[]").rstrip(".")
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return h.endswith(LOCAL_SUFFIXES) or ("." not in h and h not in ("localhost", ""))
    return (ip.is_private or ip.is_link_local) and not ip.is_loopback and not ip.is_unspecified


def _url_parts(url: str) -> tuple[str, str] | None:
    try:
        u = urlsplit(url.strip())
        _ = u.port  # raises on a bad port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    if _has_extras(u):
        return None
    return u.scheme, u.hostname


def _has_extras(u) -> bool:
    return bool(u.username or u.password or u.query or u.fragment or u.path not in ("", "/"))


def _extras_problem(url: str, what: str) -> str | None:
    """A specific message for an otherwise valid http(s) address with a path, user name or query."""
    try:
        u = urlsplit(url.strip())
        _ = u.port
    except ValueError:
        return None
    if u.scheme in ("http", "https") and u.hostname and _has_extras(u):
        return f"{what}: alleen schema, naam en eventueel poort invullen (zonder pad, gebruikersnaam of ?…)."
    return None


def normalize_server_url(url: str) -> str:
    """Canonical form of an address that passed lan_url_problem/remote_url_problem: scheme://host[:port]
    (trailing slash removed, IPv6 in brackets)."""
    u = urlsplit(url.strip())
    host = u.hostname or ""
    return f"{u.scheme}://" + (f"[{host}]" if ":" in host else host) + (f":{u.port}" if u.port else "")


def lan_url_problem(url: str) -> str | None:
    """Dutch explanation of what is wrong with a home-network address, or None when it is fine."""
    parts = _url_parts(url)
    if parts is None:
        return _extras_problem(url, "Thuisadres") or (
            "Het thuisadres is geen geldig adres (verwacht bijvoorbeeld http://192.168.1.20:8088)."
        )
    if not is_private_host(parts[1]):
        return (
            "Het thuisadres moet een adres in je thuisnetwerk zijn (bijvoorbeeld http://192.168.1.20:8088 "
            "of http://nas.local:8088), geen internetadres."
        )
    return None


def remote_url_problem(url: str) -> str | None:
    """Dutch explanation of what is wrong with the address used outside the home, or None."""
    parts = _url_parts(url)
    if parts is None:
        return _extras_problem(url, "Adres buiten huis") or (
            "Het adres buiten huis is geen geldig adres (verwacht bijvoorbeeld https://huis.voorbeeld.nl)."
        )
    if parts[0] != "https":
        return "Het adres buiten huis moet met https:// beginnen: het toegangstoken gaat dan over internet."
    if is_private_host(parts[1]) or parts[1].lower() == "localhost":
        return "Het adres buiten huis is een adres in je thuisnetwerk; vul het adres van je tunnel in."
    return None


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_pairing_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_pairing_code(code: str) -> str:
    c = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{c[:4]}-{c[4:]}"


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class SecretBox:
    """Encrypts device credentials at rest with AES-256-GCM.

    The key is derived from SHC_SECRET_KEY with HKDF, so rotating the master key means
    re-encrypting (see docs/onderhoud.md)."""

    VERSION = b"\x01"

    def __init__(self, master_key: str):
        if len(master_key) < 32:
            raise ValueError("SHC_SECRET_KEY moet minimaal 32 tekens zijn")
        self._key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"shc-credentials-v1").derive(
            master_key.encode()
        )
        # KLEIN-45: eigen sleutel voor koppelcodes (domeinscheiding), nooit de hoofdsleutel zelf.
        self._pairing_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"shc-pairing-code-v1").derive(
            master_key.encode()
        )

    def pairing_code_hash(self, code: str) -> str:
        """Opzoekwaarde van een koppelcode in ``pairing_codes.code_hash``: HMAC-SHA256 (64 tekens hex) met
        een uit SHC_SECRET_KEY afgeleide sleutel. Een code is maar ~40 bit; kale SHA-256 zou uit een gelekte
        hash binnen de geldigheid terug te rekenen zijn, deze HMAC niet zonder de serversleutel."""
        return hmac.new(self._pairing_key, code.encode(), hashlib.sha256).hexdigest()

    def encrypt(self, data: dict[str, Any]) -> bytes:
        nonce = os.urandom(12)
        ct = AESGCM(self._key).encrypt(nonce, json.dumps(data).encode(), self.VERSION)
        return self.VERSION + nonce + ct

    def decrypt(self, blob: bytes) -> dict[str, Any]:
        if not blob or blob[:1] != self.VERSION:
            raise ValueError("Onbekend versleutelingsformaat")
        nonce, ct = blob[1:13], blob[13:]
        return json.loads(AESGCM(self._key).decrypt(nonce, ct, self.VERSION))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
