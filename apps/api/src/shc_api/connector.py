"""Verificatie, sleutelbevestiging en replaycontrole voor de eigenaar-connector."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .commissioning import lock_commissioning_shared
from .contract_http import contract_error
from .hub_keys import (
    HubKeyset,
    decode_b64url,
    fingerprints_match,
    parse_pasted_keyset,
    rotated_keyset,
    utc_seconds,
)
from .models import ConnectorHubPin, ConnectorNonce, IdempotencyKey, utcnow

_POLICY_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_REQUEST_FIELDS = {"proposal", "approval", "policy_decision", "issued_at", "expires_at", "nonce", "signature"}
_REQUIRED_PROPOSAL = {"idempotency_key", "action_id", "target", "parameters", "actor", "expected_version"}
_PROPOSAL_FIELDS = _REQUIRED_PROPOSAL | {"reason", "risk_claim", "evidence_ids", "expires_in_seconds"}
_APPROVAL_FIELDS = {
    "approval_id",
    "request_id",
    "preview_hash",
    "resource_etag",
    "policy_hash",
    "risk_class",
    "approved_by",
    "approved_at",
    "expires_at",
    "one_time_nonce",
    "backup_checkpoint_id",
    "action_scope",
}
_RISK_CLASSES = ("R0", "R1", "R2", "R3")


@dataclass(frozen=True)
class PinStatus:
    fingerprint: str
    sequence: int
    accept_from: datetime


@dataclass(frozen=True)
class RotationPreview:
    previous: PinStatus
    fingerprint: str
    sequence: int


@dataclass(frozen=True)
class VerifiedRequest:
    """Geauthenticeerde inhoud voor de onafhankelijke lokale actiecontroles."""

    proposal: dict[str, Any]
    approval: dict[str, Any] | None
    risk_class: str
    policy_hash: str


def _status(pin: ConnectorHubPin | None) -> PinStatus | None:
    if pin is None:
        return None
    return PinStatus(pin.fingerprint, pin.rotation_sequence, pin.accept_from)


async def pinned_status(db: AsyncSession) -> PinStatus | None:
    return _status(await db.get(ConnectorHubPin, 1, populate_existing=True))


def _unavailable():
    return contract_error(501, "capability_unavailable", "Er is geen Hub-sleutelset bevestigd.")


def _stored_keyset(pin: ConnectorHubPin) -> HubKeyset:
    try:
        keyset = HubKeyset.from_keys(pin.jwt_keys, pin.policy_keys, pin.rotation_sequence)
        if keyset.fingerprint != pin.fingerprint:
            raise ValueError
    except ValueError:
        raise contract_error(
            503, "connector_pins_unavailable", "De opgeslagen Hub-sleutelset is onbruikbaar."
        ) from None
    return keyset


def _check_fingerprint(keyset: HubKeyset, entered: str) -> None:
    if not fingerprints_match(keyset.fingerprint, entered):
        raise contract_error(422, "vingerafdruk_verschilt", "De vingerafdruk komt niet overeen met de Hub-sleutelset.")


def _pin_changed():
    return contract_error(409, "sleutelset_gewijzigd", "De Hub-sleutelset is gewijzigd. Controleer haar opnieuw.")


async def _locked_pin(db: AsyncSession, *, read: bool = False) -> ConnectorHubPin | None:
    # De hersteltransactie neemt deze installatielock exclusief, vóór de tabellen.
    await lock_commissioning_shared(db)
    return await db.get(ConnectorHubPin, 1, with_for_update={"read": read}, populate_existing=True)


async def _save_pin(
    db: AsyncSession,
    keyset: HubKeyset,
    old: ConnectorHubPin | None,
    accept_from: datetime,
) -> PinStatus:
    values = {
        "jwt_keys": [key.jwk() for key in keyset.jwt],
        "policy_keys": [key.jwk() for key in keyset.policy],
        "fingerprint": keyset.fingerprint,
        "rotation_sequence": keyset.sequence,
        "accept_from": accept_from,
    }
    if old is None:
        stmt = insert(ConnectorHubPin).values(id=1, **values).on_conflict_do_nothing().returning(ConnectorHubPin.id)
        if await db.scalar(stmt) is None:
            raise _pin_changed()
    else:
        for field, value in values.items():
            setattr(old, field, value)
        await db.flush()
    return PinStatus(keyset.fingerprint, keyset.sequence, accept_from)


async def confirm_paste(
    db: AsyncSession,
    source: str,
    fingerprint: str,
    *,
    replacing: PinStatus | None,
) -> PinStatus:
    """Roep alleen aan na beheerderbevestiging van de vingerafdruk. Toon de status na de commit."""
    try:
        keyset = parse_pasted_keyset(source)
    except ValueError:
        raise contract_error(422, "sleutelset_ongeldig", "De geplakte Hub-sleutelset is ongeldig.") from None
    _check_fingerprint(keyset, fingerprint)
    old = await _locked_pin(db)
    if _status(old) != replacing:
        raise _pin_changed()
    if old is not None and keyset.sequence < old.rotation_sequence:
        current = _stored_keyset(old)
        old_keys = {key.x for key in (*current.jwt, *current.policy)}
        if any(key.x in old_keys for key in (*keyset.jwt, *keyset.policy)):
            raise contract_error(
                409, "rotatievolgorde_ouder", "Gebruik de actuele rotatievolgorde van de gekoppelde Hub."
            )
    # De eerste volledige seconde na bevestiging sluit eerder uitgegeven verzoeken uit, ook na herstel.
    accept_from = utcnow().replace(microsecond=0) + timedelta(seconds=1)
    return await _save_pin(db, keyset, old, accept_from)


def _rotation(pin: ConnectorHubPin, items: Any) -> HubKeyset:
    keyset = _stored_keyset(pin)
    try:
        return rotated_keyset(keyset, items, utcnow())
    except ValueError:
        raise contract_error(
            422, "sleutelrotatie_ongeldig", "De ondertekende Hub-sleutelrotatie is ongeldig."
        ) from None


async def preview_rotation(db: AsyncSession, items: Any) -> RotationPreview:
    pin = await db.get(ConnectorHubPin, 1, populate_existing=True)
    if pin is None:
        raise _unavailable()
    keyset = _rotation(pin, items)
    return RotationPreview(
        PinStatus(pin.fingerprint, pin.rotation_sequence, pin.accept_from), keyset.fingerprint, keyset.sequence
    )


async def confirm_rotation(
    db: AsyncSession,
    items: Any,
    fingerprint: str,
    *,
    replacing: PinStatus,
) -> PinStatus:
    """Controleer de reeks opnieuw tegen de huidige pin en bewaar haar pas na beheerderbevestiging."""
    pin = await _locked_pin(db)
    if pin is None or _status(pin) != replacing:
        raise _pin_changed()
    keyset = _rotation(pin, items)
    _check_fingerprint(keyset, fingerprint)
    return await _save_pin(db, keyset, pin, pin.accept_from)


def _canonical(value: Any) -> bytes:
    def check(item: Any, depth: int = 0) -> None:
        if depth > 32:
            raise ValueError
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif item is not None and not isinstance(item, str | int):
            raise ValueError

    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _uuid(value: Any, *, header: bool = False) -> uuid.UUID:
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError
    parsed = uuid.UUID(value)
    if str(parsed) != (value.lower() if header else value):
        raise ValueError
    return parsed


def _hash_format(value: Any) -> bool:
    return isinstance(value, str) and _POLICY_HASH.fullmatch(value) is not None


def _validate_approval_shape(approval: Any) -> None:
    if approval is None:
        return
    if (
        not isinstance(approval, dict)
        or set(approval) != _APPROVAL_FIELDS
        or not _hash_format(approval["preview_hash"])
        or not _hash_format(approval["policy_hash"])
        or approval["risk_class"] not in _RISK_CLASSES
        or not isinstance(approval["action_scope"], dict)
        or not isinstance(approval["approved_by"], str)
        or not approval["approved_by"]
        or not isinstance(approval["resource_etag"], str)
        or approval["backup_checkpoint_id"] is not None
        and not isinstance(approval["backup_checkpoint_id"], str)
    ):
        raise ValueError
    for field in ("approval_id", "request_id", "one_time_nonce"):
        _uuid(approval[field])
    utc_seconds(approval["approved_at"])
    utc_seconds(approval["expires_at"])


def _validate_envelope(request: Any) -> None:
    if not isinstance(request, dict) or set(request) != _REQUEST_FIELDS:
        raise ValueError
    proposal, decision = request["proposal"], request["policy_decision"]
    if (
        not isinstance(proposal, dict)
        or not _REQUIRED_PROPOSAL <= proposal.keys() <= _PROPOSAL_FIELDS
        or not isinstance(proposal["action_id"], str)
        or not isinstance(proposal["expected_version"], str)
        or not all(isinstance(proposal[field], dict) for field in ("actor", "target", "parameters"))
        or not all(isinstance(proposal["actor"].get(field), str) for field in ("type", "id"))
    ):
        raise ValueError
    _uuid(proposal["idempotency_key"])
    if (
        not isinstance(decision, dict)
        or set(decision) != {"risk_class", "rule_ids", "policy_hash"}
        or decision["risk_class"] not in _RISK_CLASSES
        or not isinstance(decision["rule_ids"], list)
        or not all(isinstance(rule, str) for rule in decision["rule_ids"])
        or not _hash_format(decision["policy_hash"])
    ):
        raise ValueError
    _validate_approval_shape(request["approval"])


def _signed_content(request: Any, keyset: HubKeyset) -> tuple[dict[str, Any], bytes, datetime, datetime, uuid.UUID]:
    try:
        _validate_envelope(request)
        sig = decode_b64url(request["signature"], length=64)
        message = _canonical({field: value for field, value in request.items() if field != "signature"})
        issued = utc_seconds(request["issued_at"])
        expires = utc_seconds(request["expires_at"])
        nonce = _uuid(request["nonce"])
    except ValueError:
        raise contract_error(
            422, "execution_request_ongeldig", "Het uitvoeringsverzoek heeft niet de juiste vorm."
        ) from None
    if not any(key.verifies(sig, message) for key in keyset.policy):
        raise contract_error(
            403, "handtekening_ongeldig", "Het verzoek is niet ondertekend met een gepinde policy-sleutel."
        )
    # ADR-0016, Meldingen bij het contract, optie B. De handtekening bindt de vormgecontroleerde
    # policy_hash aan dit verzoek. De actie zelf gaat door het onafhankelijke lokale beleid.
    return json.loads(message), hashlib.sha256(sig + message).digest(), issued, expires, nonce


def _check_times(issued: datetime, expires: datetime, accept_from: datetime) -> None:
    now = utcnow()
    if issued > now:
        raise contract_error(403, "verzoek_nog_niet_geldig", "Het uitvoeringsverzoek is in de toekomst uitgegeven.")
    if expires <= now:
        raise contract_error(403, "verzoek_verlopen", "Het uitvoeringsverzoek is verlopen.")
    if expires <= issued or expires - issued > timedelta(minutes=15):
        raise contract_error(403, "verzoek_geldigheid_ongeldig", "Een uitvoeringsverzoek is hooguit 15 minuten geldig.")
    if issued < accept_from:
        raise contract_error(
            403, "verzoek_voor_koppeling", "Het uitvoeringsverzoek is vóór de actieve koppeling uitgegeven."
        )


async def process_request(
    db: AsyncSession,
    *,
    scope: str,
    idempotency_key: str | None,
    request: Any,
    operation: Callable[[AsyncSession, VerifiedRequest], Awaitable[tuple[int, dict[str, Any]]]],
) -> tuple[int, dict[str, Any], bool]:
    """Gebruik na authenticatie een vaste scope per route en geauthenticeerde aanroeper.

    De operatie controleert het lokale beleid en plant alleen werk in dezelfde transactie in.
    De route commit vóór het antwoorden. De derde retourwaarde markeert een idempotente herhaling.
    """
    async with db.begin_nested():
        pin = await _locked_pin(db, read=True)
        if pin is None:
            raise _unavailable()
        body, digest, issued, expires, nonce = _signed_content(request, _stored_keyset(pin))
        if idempotency_key is None:
            raise contract_error(400, "idempotency_key_required", "De header Idempotency-Key ontbreekt.")
        try:
            key = _uuid(idempotency_key, header=True)
        except ValueError:
            raise contract_error(
                400, "idempotency_key_invalid", "De header Idempotency-Key moet een UUID zijn."
            ) from None

        reserve = (
            insert(IdempotencyKey)
            .values(scope=scope, key=key, request_hash=digest)
            .on_conflict_do_nothing()
            .returning(IdempotencyKey.key)
        )
        if await db.scalar(reserve) is None:
            stored = await db.get(IdempotencyKey, (scope, key), populate_existing=True)
            assert stored is not None
            if stored.request_hash != digest:
                raise contract_error(
                    422, "idempotency_key_reused", "Deze idempotentiesleutel hoort bij een ander verzoek."
                )
            if stored.response_status is None or stored.response_body is None:
                raise contract_error(409, "idempotency_in_progress", "Dit verzoek is nog in behandeling.")
            return stored.response_status, stored.response_body, True

        _check_times(issued, expires, pin.accept_from)
        consume = insert(ConnectorNonce).values(nonce=nonce).on_conflict_do_nothing().returning(ConnectorNonce.nonce)
        if await db.scalar(consume) is None:
            raise contract_error(409, "nonce_al_gebruikt", "Dit uitvoeringsverzoek is al gebruikt.")
        # Wachtte de nonce op een afgebroken transactie, controleer dan de geldigheid opnieuw.
        _check_times(issued, expires, pin.accept_from)

        proposal, decision = body["proposal"], body["policy_decision"]
        proposal.pop("risk_claim", None)
        verified = VerifiedRequest(proposal, body["approval"], decision["risk_class"], decision["policy_hash"])
        status, response = await operation(db, verified)
        await db.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
            .values(response_status=status, response_body=response)
        )
        return status, response, False
