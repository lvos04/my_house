"""Publieke Hub-sleutels en handmatig bevestigde rotatie volgens contract 1.3 §4.5."""

import base64
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
FINGERPRINT_ALIASES = str.maketrans({"O": "0", "I": "1", "L": "1", "-": None, " ": None})
BASE64URL = re.compile(r"[a-zA-Z0-9_-]+")
UTC_SECONDS = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def encode_b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode_b64url(value: Any, *, length: int | None = None) -> bytes:
    if not isinstance(value, str) or not BASE64URL.fullmatch(value):
        raise ValueError("Ongeldige base64url.")
    if length is not None and len(value) != (length * 8 + 5) // 6:
        raise ValueError("Ongeldige lengte van base64url.")
    data = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if (length is not None and len(data) != length) or encode_b64url(data) != value:
        raise ValueError("Ongeldige base64url.")
    return data


def utc_seconds(value: Any) -> datetime:
    if not isinstance(value, str) or not UTC_SECONDS.fullmatch(value):
        raise ValueError("Verwacht een UTC-tijd in hele seconden.")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Dubbele JSON-velden.")
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("Ongeldige JSON-waarde.")


def _read_object(source: str | bytes) -> dict[str, Any]:
    if not isinstance(source, str | bytes):
        raise ValueError("Verwacht een JSON-object.")
    if isinstance(source, bytes):
        source = source.decode("utf-8")
    try:
        value = json.loads(source, object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
    except RecursionError:
        raise ValueError("JSON is te diep genest.") from None
    if not isinstance(value, dict):
        raise ValueError("Verwacht een JSON-object.")
    return value


@dataclass(frozen=True)
class HubKey:
    kid: str
    x: str

    @classmethod
    def from_jwk(cls, value: Any) -> "HubKey":
        if (
            not isinstance(value, dict)
            or value.get("kty") != "OKP"
            or value.get("crv") != "Ed25519"
            or "d" in value
            or value.get("use", "sig") != "sig"
            or value.get("alg", "EdDSA") != "EdDSA"
        ):
            raise ValueError("Verwacht een publieke Ed25519-ondertekeningssleutel.")
        kid = value.get("kid")
        if not isinstance(kid, str) or not kid or not kid.isprintable():
            raise ValueError("De sleutel-ID ontbreekt of is ongeldig.")
        x = value.get("x")
        decode_b64url(x, length=32)
        return cls(kid, x)

    def jwk(self) -> dict[str, str]:
        return {"crv": "Ed25519", "kty": "OKP", "x": self.x, "kid": self.kid}

    def thumbprint(self) -> bytes:
        data = json.dumps({"crv": "Ed25519", "kty": "OKP", "x": self.x}, separators=(",", ":")).encode("ascii")
        return encode_b64url(hashlib.sha256(data).digest()).encode("ascii")

    def verifies(self, signature: bytes, message: bytes) -> bool:
        key = Ed25519PublicKey.from_public_bytes(decode_b64url(self.x, length=32))
        try:
            key.verify(signature, message)
        except InvalidSignature:
            return False
        return True


def _key_list(values: Any) -> tuple[HubKey, ...]:
    if not isinstance(values, list) or not values:
        raise ValueError("Voor elk doel is ten minste één sleutel nodig.")
    keys = tuple(HubKey.from_jwk(value) for value in values)
    if len({key.kid for key in keys}) != len(keys) or len({key.x for key in keys}) != len(keys):
        raise ValueError("De sleutelset bevat dubbele sleutels of sleutel-ID's.")
    return keys


@dataclass(frozen=True)
class HubKeyset:
    jwt: tuple[HubKey, ...]
    policy: tuple[HubKey, ...]
    sequence: int

    def __post_init__(self) -> None:
        if {key.x for key in self.jwt}.intersection(key.x for key in self.policy):
            raise ValueError("JWT en policy moeten verschillend publiek sleutelmateriaal gebruiken.")

    @classmethod
    def from_keys(cls, jwt: Any, policy: Any, sequence: Any) -> "HubKeyset":
        if type(sequence) is not int or not 0 <= sequence < 2**63:
            raise ValueError("Ongeldige volgorde van sleutelrotaties.")
        return cls(_key_list(jwt), _key_list(policy), sequence)

    @property
    def fingerprint(self) -> str:
        jwt = b",".join(sorted(key.thumbprint() for key in self.jwt))
        policy = b",".join(sorted(key.thumbprint() for key in self.policy))
        digest = hashlib.sha256(b"jl-hub-keyset-v1\njwt:" + jwt + b"\npolicy:" + policy).digest()
        prefix = int.from_bytes(digest[:10], "big")
        code = "".join(CROCKFORD[(prefix >> offset) & 31] for offset in range(75, -1, -5))
        return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


def fingerprints_match(expected: str, entered: Any) -> bool:
    return (
        isinstance(entered, str)
        and entered.isascii()
        and expected.replace("-", "") == entered.upper().translate(FINGERPRINT_ALIASES)
    )


def parse_pasted_keyset(source: str, *, require_sequence: bool = False) -> HubKeyset:
    data = _read_object(source)
    jwks, policy = data.get("jwks"), data.get("policy_public_key")
    if not isinstance(jwks, dict):
        raise ValueError("De geplakte Hub-sleutelset bevat geen JWKS-object.")
    if isinstance(policy, dict):
        policy = [policy]
    sequence = data.get("rotation_sequence", None if require_sequence else 0)
    return HubKeyset.from_keys(jwks.get("keys"), policy, sequence)


def parse_pasted_rotations(source: str) -> Any:
    return _read_object(source).get("items")


def rotated_keyset(current: HubKeyset, items: Any, now: datetime) -> HubKeyset:
    if not isinstance(items, list) or not items:
        raise ValueError("Er ontbreekt een rotatieverklaring.")
    for item in items:
        current = _apply_rotation(current, item, now)
    return current


def _apply_rotation(current: HubKeyset, item: Any, now: datetime) -> HubKeyset:
    sequence = current.sequence + 1
    if (
        not isinstance(item, dict)
        or type(item.get("sequence")) is not int
        or item["sequence"] != sequence
        or sequence >= 2**63
        or not isinstance(item.get("statement"), str)
    ):
        raise ValueError("Rotatieverklaringen moeten opeenvolgend zijn.")
    encoded_header, encoded_body, encoded_sig = item["statement"].split(".")
    header = _read_object(decode_b64url(encoded_header))
    body = _read_object(decode_b64url(encoded_body))
    if (
        set(header) != {"alg", "kid", "typ"}
        or header["alg"] != "EdDSA"
        or header["typ"] != "hub-key-rotation+jws"
        or set(body) != {"type", "iss", "sequence", "issued_at", "purpose", "add", "retire"}
        or body["type"] != "hub_key_rotation"
        or body["iss"] != "hub"
        or type(body["sequence"]) is not int
        or body["sequence"] != sequence
        or utc_seconds(body["issued_at"]) > now
        or body["purpose"] not in ("jwt", "policy")
    ):
        raise ValueError("Ongeldige rotatieverklaring.")
    signature = decode_b64url(encoded_sig, length=64)
    message = f"{encoded_header}.{encoded_body}".encode("ascii")
    purpose = body["purpose"]
    keys = current.jwt if purpose == "jwt" else current.policy
    if not any(key.kid == header["kid"] and key.verifies(signature, message) for key in keys):
        raise ValueError("De rotatie mist een geldige handtekening van een gepinde sleutel voor dit doel.")
    add, retire = body["add"], body["retire"]
    known = {key.kid for key in keys}
    if (
        not isinstance(add, list)
        or not isinstance(retire, list)
        or not (add or retire)
        or any(not isinstance(kid, str) or kid not in known for kid in retire)
        or len(set(retire)) != len(retire)
    ):
        raise ValueError("Ongeldige wijzigingen in de sleutelset.")
    new_keys = _key_list([key.jwk() for key in keys if key.kid not in retire] + add)
    if set(new_keys) == set(keys):
        raise ValueError("De rotatie wijzigt geen sleutel.")
    return replace(current, sequence=sequence, **{purpose: new_keys})
