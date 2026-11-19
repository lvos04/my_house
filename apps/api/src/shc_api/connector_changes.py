"""Duurzame voorstellen en herstel van geverifieerde apparaatstanden."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from shc_integrations.gree import GreeAdapter
from sqlalchemy import Text, cast, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from starlette.exceptions import HTTPException

from .commanding import queue_zone_setpoint
from .connector import RESTORED_COMMAND_DETAIL, VerifiedRequest, bounded_reservation
from .connector_actions import ACTIONS, Target, check_target_policy, not_found, resolve_target
from .connector_proof import apply_preview, digest
from .contract_http import contract_error
from .errors import require_secret_box
from .hub_keys import utc_seconds
from .models import (
    ClimateZone,
    CommandRecord,
    ConnectorChange,
    ConnectorHubPin,
    ConnectorNonce,
    Device,
    IdempotencyKey,
    Integration,
    ScheduleBlock,
    utcnow,
)
from .policy import PolicyDenied
from .routers.devices import _adapter_for, _close
from .routers.zones import block_view
from .views.common import caps_of, pending_command

MAX_AGE_S = 120
TTL = timedelta(seconds=MAX_AGE_S)
SETPOINT = "zone.command"
CONCEPT = "zone.schedule.propose"
SOURCE = "connector"
LOCAL_PROPOSAL = "connector:voorstel:2"
# Voor queued is updated_at het eerst toegestane claimtijdstip (ook voor gewone retries).
# De oude worker laat deze reservering staan. De bevoegde worker zet bij claim het echte tijdstip.
WAIT_FOR_CONNECTOR_WORKER = datetime(9999, 1, 1, tzinfo=UTC)
UNCERTAIN = {
    "readback_unavailable", "readback_unknown_after_restart", "readback_unknown_after_restore",
    "readback_mismatch", "readback_side_effect", "readback_incomplete",
}
NOT_SENT = {"precondition_failed", "readback_before_send_unavailable", "refused_before_send"}


def _stale():
    return contract_error(
        409, "precondition_failed", "De stand of configuratie is gewijzigd. Vraag een nieuw voorstel.",
    )


def _unavailable():
    return contract_error(
        501, "capability_unavailable", "Voor deze actie is veilig apparaatherstel niet beschikbaar.",
    )


async def _schedule(db: AsyncSession, zone: ClimateZone) -> list[dict]:
    blocks = await db.scalars(select(ScheduleBlock).where(ScheduleBlock.zone_id == zone.id)
                              .order_by(ScheduleBlock.start, ScheduleBlock.id))
    return [block_view(block) for block in blocks]


async def _integration(db: AsyncSession, device: Device) -> Integration | None:
    if device.integration_id is None:
        return None
    integration = await db.get(Integration, device.integration_id, populate_existing=True)
    if integration is None or not integration.enabled or integration.home_id != device.home_id:
        return None
    return integration


async def _binding(db: AsyncSession, target: Target) -> str:
    resource = target.zone or target.device
    device = target.device
    integration = await _integration(db, device) if device else None
    return digest({
        "resource": str(resource.id),
        "version": resource.connector_version,
        "device": [str(device.id), device.connector_version] if device else None,
        "integration": [str(integration.id), integration.config,
                        hashlib.sha256(integration.credentials_enc or b"").hexdigest()] if integration else None,
        "schedule": await _schedule(db, target.zone) if target.zone else None,
    })


async def remember_proposal_fields(conn: AsyncConnection) -> None:
    """Koppel ook oude metingen zonder opdracht aan de unit als de opgeslagen ETag haar identiteit bevestigt."""
    async with AsyncSession(bind=conn) as db:
        rows = await db.execute(
            select(ClimateZone, Device, ConnectorChange.before, ConnectorChange.resource_etag)
            .select_from(ConnectorChange)
            .join(ClimateZone, cast(ClimateZone.id, Text) == ConnectorChange.target["zone_id"].astext)
            .join(Device, Device.id == ClimateZone.unit_device_id)
            .where(ConnectorChange.action_id == SETPOINT, ConnectorChange.command_id.is_(None),
                   ConnectorChange.before.has_key("controls"), Device.provider == "gree")
        )
        bindings = {}
        for zone, device, before, etag in rows:
            known = device.gree_control_fields()
            missing = before["controls"].keys() - known
            if not missing:
                continue
            if zone.id not in bindings:
                bindings[zone.id] = await _binding(db, Target(ACTIONS[SETPOINT], zone, device, {}))
            if digest({"binding": bindings[zone.id], "before": before}) == etag:
                device.state = {**(device.state or {}), "gree_control_fields": sorted(known | missing)}
        await db.flush()


async def read_setpoint(adapter, device: Device, *, remember_fields: bool = False) -> dict | None:
    if not isinstance(adapter, GreeAdapter):
        return None
    try:
        async with asyncio.timeout(8):
            state = await adapter.read_recoverable_setpoint(device.stable_identity, device.gree_control_fields())
            fields = sorted(adapter.control_fields())
            if remember_fields and (device.state or {}).get("gree_control_fields") != fields:
                device.state = {**(device.state or {}), "gree_control_fields": fields}
            return state
    except Exception:
        # Antwoorden en adapterfouten kunnen adressen of protocolgegevens bevatten.
        return None


def _credentials(integration: Integration) -> dict:
    return require_secret_box().decrypt(integration.credentials_enc) if integration.credentials_enc else {}


async def _read_current(db: AsyncSession, device: Device, *, remember_fields: bool = False) -> dict | None:
    integration = await _integration(db, device)
    if integration is None or integration.provider != "gree":
        return None
    try:
        adapter = _adapter_for(integration, _credentials(integration))
    except Exception:
        # Geef geen configuratie terug als de opgeslagen koppeling onleesbaar is.
        return None
    try:
        return await read_setpoint(adapter, device, remember_fields=remember_fields)
    finally:
        await _close(adapter)


async def _recoverable(db: AsyncSession, target: Target, *, exclude_id=None) -> bool:
    zone, device = target.zone, target.device
    return bool(
        zone and device and device.provider == "gree" and device.kind == "ac_unit"
        and zone.control == "manual" and zone.override_until and zone.override_until > utcnow()
        and set(target.params) == {"target_c"}
        and not await pending_command(db, device.id, exclude_id=exclude_id, with_mode=False)
    )


def _compatible(target: Target, state: dict) -> bool:
    caps = caps_of(target.device)
    return (
        state["power"] is True and state["mode"] in caps.modes
        and caps.min_c <= state["target_c"] <= caps.max_c
        and state["target_c"] % caps.step_c == 0 and target.params["target_c"] % caps.step_c == 0
    )


async def propose(db: AsyncSession, req: VerifiedRequest, client: str) -> tuple[int, dict]:
    prop = req.proposal
    target = await resolve_target(db, prop, req.risk_class)
    proposal_key = uuid.UUID(prop["idempotency_key"])
    action = prop["action_id"]
    before, observed_at, reason = None, None, None
    binding = await _binding(db, target)
    if action == CONCEPT:
        before = {"schedule": await _schedule(db, target.zone)}
        observed_at = utcnow()
    elif action == SETPOINT and await _recoverable(db, target):
        before = await _read_current(db, target.device, remember_fields=True)
        observed_at = utcnow() if before is not None else None
        if before is None or not _compatible(target, before):
            reason = "readback_unavailable"
    else:
        reason = "capability_unavailable"

    etag = digest({"binding": binding, "before": before})
    if prop["expected_version"] not in {"absent", "geen", "eigenaar", etag}:
        raise _stale()
    recipe = None if reason or action == CONCEPT else {"target_c": before["target_c"]}
    diff = {"before": before, "proposed": prop["parameters"]}
    blast_radius = [prop["target"]]
    preview_hash = apply_preview(action, diff, prop["target"], etag, recipe)
    status = "failed" if reason == "readback_unavailable" else "unavailable" if reason else "proposed"
    valid_until = min(req.expires_at, utcnow() + TTL)
    reserve = insert(ConnectorChange).values(
        client=client, proposal_key=proposal_key, action_id=action, target=prop["target"],
        parameters=prop["parameters"], actor_hash=digest(prop["actor"]), resource_etag=etag, preview_hash=preview_hash,
        before=before, observed_at=observed_at, status=status, reason_code=reason, valid_until=valid_until,
    ).on_conflict_do_nothing(
        index_elements=[ConnectorChange.client, ConnectorChange.proposal_key],
    ).returning(ConnectorChange.id)
    identity = await bounded_reservation(db, lambda: db.scalar(reserve))
    if identity is None:
        raise contract_error(409, "conflict", "Deze voorstel-ID is al gebruikt.")
    if status == "proposed" and action == SETPOINT:
        # Dit ontvangstbewijs blijft lokaal, net als de andere idempotentierijen en de Hub-pin.
        db.add(IdempotencyKey(
            scope=LOCAL_PROPOSAL, key=identity, request_hash=bytes.fromhex(preview_hash.removeprefix("sha256:")),
        ))
    return 200, {
        "proposal_id": str(identity), "preview_hash": preview_hash, "resource_etag": etag,
        "diff": diff, "before": before, "blast_radius": blast_radius, "risk_claim": target.action.risk,
        "rollback_recipe": recipe, "requires_backup": False,
        "observed_at": observed_at.isoformat() if observed_at else None,
        "valid_until": valid_until.isoformat(), "status": status,
        "reason_code": reason, "concept_only": action == CONCEPT,
    }


async def _approval(
    db, req: VerifiedRequest, change: ConnectorChange, preview_hash: str, parameters: dict,
) -> datetime:
    prop, data = req.proposal, req.approval
    if data is None:
        raise contract_error(403, "approval_required", "Deze actie vraagt een menselijke goedkeuring.")
    now = utcnow()
    approved, expires = utc_seconds(data["approved_at"]), utc_seconds(data["expires_at"])
    if not approved <= now < expires <= approved + timedelta(minutes=15):
        raise contract_error(403, "approval_expired", "De goedkeuring is nog niet of niet meer geldig.")
    if (
        data["request_id"] != str(change.proposal_key)
        or data["policy_hash"] != req.policy_hash or data["risk_class"] != req.risk_class
        or data["resource_etag"] != change.resource_etag or data["preview_hash"] != preview_hash
        or data["action_scope"] != {
            "action_id": prop["action_id"], "target": prop["target"], "parameters_sha256": digest(parameters),
        }
    ):
        raise contract_error(409, "approval_mismatch", "De goedkeuring hoort niet bij dit voorstel.")
    nonce = uuid.UUID(data["one_time_nonce"])
    consume = insert(ConnectorNonce).values(nonce=nonce).on_conflict_do_nothing().returning(ConnectorNonce.nonce)
    if await bounded_reservation(db, lambda: db.scalar(consume)) is None:
        raise contract_error(409, "nonce_al_gebruikt", "Deze goedkeuring is al gebruikt.")
    return min(req.expires_at, expires)


async def _queue(db: AsyncSession, target: Target, temperature: float) -> CommandRecord:
    try:
        cmd = await queue_zone_setpoint(db, target.zone, target.device, temperature, source=SOURCE, actor="Hub")
        cmd.updated_at = WAIT_FOR_CONNECTOR_WORKER
        return cmd
    except PolicyDenied as exc:
        raise contract_error(409, exc.code, exc.message) from None


async def _active_binding(db: AsyncSession, change: ConnectorChange) -> bool:
    pin = await db.get(ConnectorHubPin, 1, populate_existing=True)
    if pin is None or change.created_at < pin.accept_from:
        return False
    local = await db.get(IdempotencyKey, (LOCAL_PROPOSAL, change.id))
    return local is not None and f"sha256:{local.request_hash.hex()}" == change.preview_hash


async def _change_for_request(db, req: VerifiedRequest, client: str) -> tuple[Target, ConnectorChange]:
    prop = req.proposal
    # Weiger beschermde doelen vóór alle overige precondities, ook zonder voorstel.
    target = await resolve_target(db, prop, req.risk_class)
    if prop["action_id"] not in {SETPOINT, CONCEPT}:
        raise _unavailable()
    change = await db.scalar(select(ConnectorChange).where(
        ConnectorChange.client == client, ConnectorChange.proposal_key == uuid.UUID(prop["idempotency_key"]),
    ).with_for_update())
    if change is None:
        raise contract_error(409, "proposal_required", "Een geldig voorstel is verplicht.")
    if (
        change.action_id != prop["action_id"] or change.target != prop["target"]
        or change.parameters != prop["parameters"] or change.actor_hash != digest(prop["actor"])
        or change.resource_etag != prop["expected_version"]
    ):
        raise _stale()
    if change.action_id == SETPOINT and (change.status == "proposed" or change.command_id is not None):
        if not await _active_binding(db, change):
            raise contract_error(409, "proposal_unavailable", "Dit voorstel hoort bij een eerdere Hub-koppeling.")
    return target, change


async def apply(db: AsyncSession, req: VerifiedRequest, client: str) -> tuple[int, dict]:
    target, change = await _change_for_request(db, req, client)
    if change.action_id == CONCEPT or change.reason_code == "capability_unavailable":
        raise _unavailable()
    if change.reason_code == "readback_unavailable" and change.command_id is None:
        raise contract_error(409, "readback_unavailable", "De vorige stand is niet betrouwbaar leesbaar.")
    if change.status != "proposed" or change.valid_until <= utcnow() or "controls" not in (change.before or {}):
        raise contract_error(
            409, "proposal_unavailable", "Het voorstel is verwerkt, verlopen of zonder betrouwbare stand.",
        )
    if not await _recoverable(db, target):
        raise _unavailable()

    expires = await _approval(db, req, change, change.preview_hash, change.parameters)
    current = await _read_current(db, target.device)
    if current is None:
        change.status, change.reason_code = "failed", "readback_unavailable"
        return 409, {"code": "readback_unavailable", "message": "De actuele stand is niet betrouwbaar leesbaar.",
                     "details": {"evidence_id": str(change.id)}, "trace_id": str(uuid.uuid4())}
    if digest({"binding": await _binding(db, target), "before": current}) != change.resource_etag:
        raise _stale()
    if utcnow() >= min(expires, change.valid_until):
        raise contract_error(409, "proposal_unavailable", "Het voorstel of uitvoeringsverzoek is verlopen.")
    cmd = await _queue(db, target, target.params["target_c"])
    change.command_id, change.status = cmd.id, "queued"
    change.execution_expires_at = min(expires, change.valid_until)
    return 202, {"command_id": str(cmd.id), "evidence_id": str(change.id), "status": "queued"}


def _same_controls(first: dict, second: dict) -> bool:
    return {k: v for k, v in first.items() if k != "target_c"} == {
        k: v for k, v in second.items() if k != "target_c"
    }


async def _rollback_preview(db: AsyncSession, change: ConnectorChange) -> tuple[dict | None, str | None]:
    interrupted = "readback_unknown_after_restart"
    if (
        not change.before or "controls" not in change.before or change.execution_expires_at is None
        or change.rollback_command_id is not None and change.rollback_reason_code not in NOT_SENT | {interrupted}
        or not (change.status == "confirmed" or change.status == "failed" and change.reason_code in UNCERTAIN)
    ):
        return None, None
    after = change.after or {**change.before, "target_c": change.parameters["target_c"]}
    if change.reason_code == interrupted or change.rollback_reason_code == interrupted:
        if await _superseded(db, change, exclude_id=change.rollback_command_id):
            return None, "precondition_failed"
        original = await db.get(CommandRecord, change.command_id)
        device = await db.get(Device, original.device_id) if original else None
        measured = await _read_current(db, device) if device else None
        if measured is None:
            return None, "readback_unavailable"
        if measured not in (change.before, after):
            return None, "precondition_failed"
        after = measured
    if not _same_controls(change.before, after):
        return None, None
    parameters = {"target_c": change.before["target_c"]}
    diff = {"before": after, "after": change.before}
    preview_hash = digest({
        "direction": "rollback", "apply_preview_hash": change.preview_hash, "action_id": change.action_id,
        "target": change.target, "resource_etag": change.resource_etag, "diff": diff, "parameters": parameters,
    })
    return {"preview_hash": preview_hash, "diff": diff, "parameters": parameters}, None


def _previous_attempts(change: ConnectorChange) -> list[str]:
    return (change.rollback_from or {}).get("previous_attempts", [])


def _remember_rollback(change: ConnectorChange, command_id: uuid.UUID, current: dict, expires: datetime) -> None:
    previous = _previous_attempts(change)
    if change.rollback_command_id is not None:
        previous = [*previous, str(change.rollback_command_id)]
    change.rollback_from = {"state": current, "previous_attempts": previous}
    change.rollback_command_id, change.rollback_status, change.rollback_expires_at = command_id, "queued", expires
    change.rollback_after = change.rollback_at = change.rollback_reason_code = None


async def rollback(db: AsyncSession, req: VerifiedRequest, client: str) -> tuple[int, dict]:
    target, change = await _change_for_request(db, req, client)
    if change.action_id == CONCEPT or change.before is None or change.command_id is None:
        raise _unavailable()
    preview, reason = await _rollback_preview(db, change)
    if reason == "readback_unavailable":
        raise contract_error(409, reason, "De actuele stand is niet betrouwbaar leesbaar.")
    if reason == "precondition_failed":
        raise _stale()
    if preview is None:
        raise contract_error(409, "conflict", "Herstel is nu niet beschikbaar of is al ingepland.")
    if not await _recoverable(db, target):
        raise _unavailable()
    if await _superseded(db, change, exclude_id=change.rollback_command_id):
        raise _stale()
    expires = await _approval(db, req, change, preview["preview_hash"], preview["parameters"])
    current = await _read_current(db, target.device)
    if current is None:
        raise contract_error(409, "readback_unavailable", "De actuele stand is niet betrouwbaar leesbaar.")
    if not await _same_context(db, target, change, current, preview["diff"]["before"]):
        raise _stale()
    if utcnow() >= expires:
        raise contract_error(409, "proposal_unavailable", "Het herstelverzoek is verlopen.")
    cmd = await _queue(db, target, change.before["target_c"])
    _remember_rollback(change, cmd.id, current, expires)
    return 202, {"command_id": str(cmd.id), "evidence_id": str(change.id), "status": "queued"}


async def _superseded(db: AsyncSession, change: ConnectorChange, *, exclude_id=None) -> bool:
    original = await db.get(CommandRecord, change.command_id)
    if original is None:
        return True
    intervening = select(CommandRecord.id).where(
        CommandRecord.device_id == original.device_id, CommandRecord.created_at > original.created_at,
        CommandRecord.id.is_distinct_from(exclude_id),
    )
    previous = _previous_attempts(change)
    if previous:
        intervening = intervening.where(CommandRecord.id.not_in([uuid.UUID(item) for item in previous]))
    return bool(await db.scalar(intervening.limit(1)))


async def _same_context(db, target, change, measured, expected) -> bool:
    if measured is None or expected is None or measured != expected:
        return False
    if not _same_controls(measured, change.before):
        return False
    return digest({"binding": await _binding(db, target), "before": change.before}) == change.resource_etag


async def linked_change(db: AsyncSession, command_id: uuid.UUID) -> ConnectorChange | None:
    return await db.scalar(select(ConnectorChange).where(or_(
        ConnectorChange.command_id == command_id, ConnectorChange.rollback_command_id == command_id,
    )).with_for_update())


async def worker_target(db, cmd, dev, change) -> Target | None:
    if change.action_id != SETPOINT or set(change.target) != {"zone_id"} or not change.before:
        return None
    zone = await db.get(ClimateZone, uuid.UUID(change.target["zone_id"]), populate_existing=True)
    if (
        zone is None or zone.id != cmd.zone_id or zone.unit_device_id != dev.id
        or zone.home_id != cmd.home_id or cmd.action != "set_hvac"
    ):
        return None
    target = Target(ACTIONS[SETPOINT], zone, dev, change.parameters)
    try:
        await check_target_policy(db, dev, target.action, zone.home_id)
    except HTTPException:
        return None
    return target if await _recoverable(db, target, exclude_id=cmd.id) else None


async def guarded_send(db, cmd, dev, adapter, send) -> tuple[str, str, dict | None]:
    change = await linked_change(db, cmd.id)
    if change is None:
        return "denied", "Het vastgelegde connectorvoorstel ontbreekt.", None
    undo = cmd.id == change.rollback_command_id
    target = await worker_target(db, cmd, dev, change)
    expected = (change.rollback_from or {}).get("state") if undo else change.before
    expires = change.rollback_expires_at if undo else change.execution_expires_at
    temperature = (change.before or {}).get("target_c") if undo else change.parameters.get("target_c")
    if not await _active_binding(db, change):
        record_failure(change, undo, "restored_without_execution")
        return "failed", RESTORED_COMMAND_DETAIL, None
    if (
        target is None or expires is None or utcnow() >= expires or cmd.params != {"target_c": temperature}
        or "controls" not in (change.before or {}) or undo and await _superseded(db, change, exclude_id=cmd.id)
    ):
        record_failure(change, undo, "precondition_failed")
        return "failed", "De vastgelegde stand is niet meer bruikbaar voor veilige bediening.", None
    integration = await _integration(db, dev)
    try:
        active_adapter = (
            isinstance(adapter, GreeAdapter) and integration and integration.provider == "gree"
            and adapter.config == integration.config and adapter.credentials == _credentials(integration)
        )
    except Exception:
        active_adapter = False
    current = await read_setpoint(adapter, dev) if active_adapter else None
    if not await _same_context(db, target, change, current, expected) or utcnow() >= expires:
        reason = "readback_before_send_unavailable" if active_adapter and current is None else "precondition_failed"
        record_failure(change, undo, reason)
        return "failed", "De vastgelegde stand is niet meer bruikbaar voor veilige bediening.", None
    if undo and current == change.before:
        change.rollback_after, change.rollback_at = current, utcnow()
        change.rollback_status, change.rollback_reason_code = "confirmed", None
        return "confirmed", "De volledige vorige stand is teruggelezen.", None
    try:
        async with asyncio.timeout(12):
            status, _, adapter_state = await send()
    except TimeoutError:
        status, adapter_state = "failed", None
    after = await read_setpoint(adapter, dev)
    now = utcnow()
    intended = change.before if undo else {**change.before, "target_c": temperature}
    confirmed = status == "confirmed" and after == intended
    mismatch = "readback_mismatch" if after else "readback_unavailable"
    if after is not None and not _same_controls(after, intended):
        mismatch = "readback_side_effect"

    if undo:
        change.rollback_after, change.rollback_at = after, now if after else None
        change.rollback_status = "confirmed" if confirmed else "failed"
        change.rollback_reason_code = None if confirmed else mismatch
    else:
        change.after, change.after_at = after, now if after else None
        change.status = "confirmed" if confirmed else "failed"
        change.reason_code = None if confirmed else mismatch
        changed = after is not None and after != current
        if not confirmed and changed and now < expires and await _same_context(db, target, change, after, after):
            # Zelfde wachtrij, veiligheidsbeleid en TTL. De worker verifieert opnieuw vóór de herstelopdracht.
            try:
                rollback_cmd = await _queue(db, target, change.before["target_c"])
            except HTTPException:
                change.rollback_status, change.rollback_reason_code = "failed", "policy_denied"
            else:
                _remember_rollback(change, rollback_cmd.id, after, expires)
    if confirmed:
        return "confirmed", "", adapter_state
    if mismatch == "readback_side_effect":
        return "failed", "Een andere bedienstand is onverwacht gewijzigd. Herstel blijft open.", None
    return "failed", "De teruglezing bevestigt de stand niet.", None


def record_failure(change: ConnectorChange, undo: bool, reason: str) -> None:
    if undo:
        change.rollback_status, change.rollback_reason_code = "failed", reason
    else:
        change.status, change.reason_code = "failed", reason


async def evidence(db: AsyncSession, client: str, identity: uuid.UUID) -> dict:
    change = await db.scalar(select(ConnectorChange).where(
        ConnectorChange.client == client,
        or_(ConnectorChange.id == identity, ConnectorChange.command_id == identity,
            ConnectorChange.rollback_command_id == identity,
            ConnectorChange.rollback_from["previous_attempts"].contains([str(identity)])),
    ))
    if change is None:
        raise not_found()
    status, reason = await _evidence_status(db, change.status, change.reason_code, change.command_id)
    rollback_status, rollback_reason = await _evidence_status(
        db, change.rollback_status, change.rollback_reason_code, change.rollback_command_id,
    )
    if status == "confirmed" and "controls" not in (change.before or {}):
        status, reason = "failed", "readback_incomplete"
    if rollback_status == "confirmed" and (
        "controls" not in (change.before or {}) or change.rollback_after != change.before
    ):
        rollback_status, rollback_reason = "failed", "readback_incomplete"
    rollback_preview, preview_reason = None, None
    if await _active_binding(db, change):
        rollback_preview, preview_reason = await _rollback_preview(db, change)
    out = {
        "id": str(change.id), "action_id": change.action_id, "status": status,
        "reason_code": reason, "resource_etag": change.resource_etag,
        "preview_hash": change.preview_hash,
        "created_at": change.created_at.isoformat(), "observed_at": None,
        "rollback_status": rollback_status, "rollback_reason_code": rollback_reason,
        "command_id": str(change.command_id) if change.command_id else None,
        "rollback_command_id": str(change.rollback_command_id) if change.rollback_command_id else None,
        "previous_rollback_command_ids": _previous_attempts(change),
        "rollback_preview": rollback_preview,
    }
    if preview_reason:
        out["rollback_preview_reason_code"] = preview_reason
    for label, state, stamp in (
        ("before", change.before, change.observed_at),
        ("after", change.after, change.after_at),
        ("rollback_after", change.rollback_after, change.rollback_at),
    ):
        # Een schema is een concept; labels uit de woningconfiguratie worden niet als bewijs doorgegeven.
        out[label] = state if change.action_id != CONCEPT else None
        out[f"{label}_hash"] = digest(state) if state is not None else None
        out[f"{label}_observed_at"] = stamp.isoformat() if stamp is not None else None
    if change.rollback_command_id is not None:
        latest = change.rollback_at
    elif change.command_id is not None:
        latest = change.after_at
    else:
        latest = change.observed_at
    if latest is not None:
        out["observed_at"] = latest.isoformat()
    out["max_age_s"] = MAX_AGE_S
    out["freshness"] = "current" if latest and utcnow() - latest <= TTL else "unknown"
    out["freshness_reason"] = None
    if out["freshness"] == "unknown":
        out["freshness_reason"] = "meting_verouderd" if latest else "readback_unavailable"
    unknown_or_changed = reason == "readback_incomplete" or change.after is None or change.after != change.before
    out["recovery_required"] = (
        rollback_status in {"queued", "failed"}
        or status == "failed" and change.command_id is not None
        and reason in UNCERTAIN and unknown_or_changed and rollback_status != "confirmed"
    )
    return out


async def _evidence_status(db, status, reason, cmd_id):
    if status == "queued":
        cmd = await db.get(CommandRecord, cmd_id)
        if cmd is None or cmd.status == "cancelled":
            if cmd and cmd.detail == RESTORED_COMMAND_DETAIL:
                return "failed", "restored_without_execution"
            return "failed", "command_cancelled"
    return status, reason
