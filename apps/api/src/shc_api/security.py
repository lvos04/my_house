from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Any

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
