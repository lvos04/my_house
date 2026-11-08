"""Gree local UDP protocol (port 7000): packet encryption and transport.

Two encryption schemes exist in the field:
- v1: AES-128-ECB with PKCS7 padding, base64 `pack`.
- v2: AES-128-GCM with a fixed nonce and AAD, base64 `pack` + base64 `tag`
  (newer wifi modules). The generic keys are only used for scan/bind; after
  binding each unit has its own key.

Some newer modules answer the scan in the v1 format but only accept a v2 bind, so
the format of the scan answer says nothing reliable about the bind encryption; the
adapter tries v1 first and then v2 (like the reference library).
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..base import AdapterError

GENERIC_KEY_V1 = b"a3K8Bx%2r8Y7#xDh"
GENERIC_KEY_V2 = b"{yxAHAY_Lm6pbC/<"
GCM_NONCE = bytes([0x54, 0x40, 0x78, 0x44, 0x49, 0x67, 0x5A, 0x51, 0x6C, 0x5E, 0x63, 0x13])
GCM_AAD = b"qualcomm-test"
DEFAULT_PORT = 7000


class GreeProtocolError(AdapterError):
    """A packet could not be decrypted or parsed (wrong key/encryption, garbage)."""

    code = "protocol_error"


def encrypt_v1(data: dict[str, Any], key: bytes) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # noqa: S305 - protocol mandated
    return base64.b64encode(enc.update(padded) + enc.finalize()).decode()


def decrypt_v1(pack: str, key: bytes) -> dict[str, Any]:
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # noqa: S305
    padded = dec.update(base64.b64decode(pack)) + dec.finalize()
    try:
        unpadder = padding.PKCS7(128).unpadder()
        raw = unpadder.update(padded) + unpadder.finalize()
    except ValueError:
        # Some firmwares pad with zero bytes / garbage; fall back to the JSON object end.
        raw = padded[: padded.rfind(b"}") + 1]
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GreeProtocolError("Kon Gree-pakket niet ontsleutelen (verkeerde sleutel?)") from exc


def encrypt_v2(data: dict[str, Any], key: bytes) -> tuple[str, str]:
    raw = json.dumps(data, separators=(",", ":")).encode()
    out = AESGCM(key).encrypt(GCM_NONCE, raw, GCM_AAD)
    ct, tag = out[:-16], out[-16:]
    return base64.b64encode(ct).decode(), base64.b64encode(tag).decode()


def decrypt_v2(pack: str, tag: str, key: bytes) -> dict[str, Any]:
    try:
        raw = AESGCM(key).decrypt(GCM_NONCE, base64.b64decode(pack) + base64.b64decode(tag), GCM_AAD)
        return json.loads(raw)
    except Exception as exc:  # InvalidTag, JSON errors
        raise GreeProtocolError("Kon Gree-pakket (GCM) niet ontsleutelen") from exc


def build_packet(inner: dict[str, Any], key: bytes, version: str, tcid: str = "", i: int = 0) -> dict[str, Any]:
    pkt: dict[str, Any] = {"cid": "app", "i": i, "t": "pack", "uid": 0, "tcid": tcid}
    if version == "v2":
        pkt["pack"], pkt["tag"] = encrypt_v2(inner, key)
    else:
        pkt["pack"] = encrypt_v1(inner, key)
    return pkt


def open_packet(outer: dict[str, Any], key: bytes) -> dict[str, Any]:
    if "pack" not in outer:
        raise GreeProtocolError("Antwoord zonder 'pack'")
    if "tag" in outer:
        return decrypt_v2(outer["pack"], outer["tag"], key)
    return decrypt_v1(outer["pack"], key)


class _OneShot(asyncio.DatagramProtocol):
    def __init__(self, collect: bool):
        self.collect = collect
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.items: list[tuple[dict[str, Any], tuple[str, int]]] = []

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            msg = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if self.collect:
            self.items.append((msg, addr))
        elif not self.future.done():
            self.future.set_result(msg)

    def error_received(self, exc: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(exc)


async def request(host: str, payload: dict[str, Any], port: int = DEFAULT_PORT, timeout: float = 3.0) -> dict[str, Any]:
    """Send one datagram and wait for the first JSON reply."""
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(lambda: _OneShot(False), remote_addr=(host, port))
    try:
        transport.sendto(json.dumps(payload).encode())
        return await asyncio.wait_for(proto.future, timeout)
    finally:
        transport.close()


async def broadcast(
    payload: dict[str, Any], address: str = "255.255.255.255", port: int = DEFAULT_PORT, timeout: float = 3.0
) -> list[tuple[dict[str, Any], tuple[str, int]]]:
    """Broadcast and collect every reply until `timeout`."""
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(
        lambda: _OneShot(True), local_addr=("0.0.0.0", 0), allow_broadcast=True
    )
    try:
        transport.sendto(json.dumps(payload).encode(), (address, port))
        await asyncio.sleep(timeout)
        return list(proto.items)
    finally:
        transport.close()
