"""Devices and integrations: list, rename, roles, calibration, archive/replace, test, diagnostics,
discovery, pairing (one integration per physical device), address changes and Zigbee setup."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update

from .. import events
from ..config import VERSION, get_settings
from ..deps import DB, Admin, Auth, get_home
from ..errors import api_error, not_found
from ..models import (
    ButtonMapping,
    ClimateZone,
    CommandRecord,
    Device,
    DeviceBinding,
    Home,
    Integration,
    LogicalRole,
    Room,
    ZoneLog,
)
from ..policy import DEFAULT_PROTECTED_ROLES
from ..security import SecretBox
from ..store import audit, latest_reading, roles_of
from ..views.status import integration_details

router = APIRouter(tags=["devices"])
log = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 15.0

# Which device kinds fit a logical role (POST /devices/{id}/bind). Roles not listed accept any kind.
ROLE_KINDS: dict[str, frozenset[str]] = {
    "grid": frozenset({"p1_meter"}),
    "airco": frozenset({"kwh_meter", "energy_socket"}),
    "boiler": frozenset({"energy_socket"}),
    "outdoor_temp": frozenset({"zigbee_sensor"}),
}
KIND_LABELS = {
    "p1_meter": "P1-meter",
    "kwh_meter": "kWh-meter",
    "energy_socket": "Energy Socket",
    "zigbee_sensor": "Zigbee-sensor",
    "ac_unit": "airco-unit",
}
SENSOR_KINDS = frozenset({"zigbee_sensor"})
CALIBRATABLE_KINDS = frozenset({"zigbee_sensor", "ac_unit"})
# Metrics shown in the device test and the diagnostics blob (latest value each).
SHOWN_METRICS = (
    "temperature",
    "humidity",
    "battery",
    "linkquality",
    "pressure",
    "power_w",
    "energy_import_kwh",
    "energy_export_kwh",
    "gas_m3",
    "target_c",
)
DEFAULT_KIND = {"gree": "ac_unit", "hue": "hue_bridge", "homewizard": "meter", "zigbee": "zigbee_device"}
# Host names accepted when pairing: an IP address in a private range, or a LAN name with one of these
# suffixes. Loopback, link-local and single-label (container) names are refused unless listed in
# SHC_PAIRING_ALLOWED_HOSTS (comma-separated), so pairing can not be used to probe the stack itself.
LAN_SUFFIXES = (".local", ".lan", ".home.arpa", ".localdomain", ".home", ".fritz.box", ".box")
_NAME_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


async def notify_changed(db: DB, integ: Integration) -> None:
    """Wake the worker's reload loop (delivered on commit) so a new address or key is used at once."""
    await events.publish(db, "integration.changed", integ.home_id, {"id": integ.id})


def providers() -> dict:
    from shc_integrations.registry import PROVIDERS

    return PROVIDERS


# --- views -------------------------------------------------------------------------------------


def allowed_roles(kind: str) -> list[str]:
    return sorted(r for r, kinds in ROLE_KINDS.items() if kind in kinds)


def _details(d: Device) -> dict:
    """Kind-specific state worth showing on the devices page."""
    s = d.state or {}
    out: dict[str, Any] = {}
    if d.kind == "energy_socket" or "relay_on" in s:
        out["relay"] = {
            "on": s.get("relay_on"),
            "switch_lock": s.get("switch_lock"),
            "state": s.get("relay_state") or ("ok" if "relay_on" in s else None),
            "led_brightness": s.get("led_brightness"),
        }
    if d.kind == "ac_unit":
        caps = s.get("caps") if isinstance(s.get("caps"), dict) else None
        out["caps"] = caps
        out["unit"] = {
            "mode": s.get("mode"),
            "target_c": s.get("target_c"),
            "indoor_c": s.get("indoor_c"),
            "indoor_sensor": s.get("indoor_sensor"),
            "fan": s.get("fan"),
            "louver": s.get("louver"),
            "display_unit": s.get("display_unit"),
        }
    if d.provider == "zigbee":
        out["zigbee"] = {
            k: s.get(k)
            for k in (
                "friendly_name",
                "interview_completed",
                "supported",
                "power_source",
                "availability",
                "bridge_state",
                "mqtt_connected",
                "zigbee2mqtt_version",
            )
            if k in s
        }
    if d.kind == "button":
        out["buttons"] = len(s.get("buttons") or [])
        out["bridge_rules"] = len(s.get("bridge_rules") or [])
    return out


async def device_view(db: DB, d: Device) -> dict:
    roles, protected = await roles_of(db, d.id)
    room = await db.get(Room, d.room_id) if d.room_id else None
    return {
        "id": d.id,
        "provider": d.provider,
        "kind": d.kind,
        "name": d.name,
        "stable_identity": d.stable_identity,
        "model": d.model,
        "firmware": d.firmware,
        "room": room.name if room else None,
        "room_id": d.room_id,
        "integration_id": d.integration_id,
        "roles": sorted(roles),
        "allowed_roles": allowed_roles(d.kind),
        "online": d.online,
        "last_seen": d.last_seen,
        "capabilities": d.capabilities or [],
        "protected": bool(protected) or bool(roles & DEFAULT_PROTECTED_ROLES),
        "compatibility": d.compatibility,
        "tested_at": d.tested_at,
        "lifecycle": d.lifecycle,
        "calibration": {
            "temperature_offset": float((d.calibration or {}).get("temperature_offset") or 0.0),
            "humidity_offset": float((d.calibration or {}).get("humidity_offset") or 0.0),
        },
        "details": _details(d),
    }


async def _device(db: DB, device_id: uuid.UUID) -> Device:
    d = await db.get(Device, device_id)
    if not d:
        raise not_found("Apparaat")
    return d


@router.get("/homes/{home_id}/devices")
async def devices(home_id: uuid.UUID, db: DB, p: Auth, include_archived: bool = False) -> list[dict]:
    await get_home(db, home_id)
    q = select(Device).where(Device.home_id == home_id).order_by(Device.provider, Device.name)
    if not include_archived:
        q = q.where(Device.lifecycle != "archived")
    return [await device_view(db, d) for d in (await db.execute(q)).scalars()]


@router.get("/devices/{device_id}")
async def get_device(device_id: uuid.UUID, db: DB, p: Auth) -> dict:
    return await device_view(db, await _device(db, device_id))


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    room_id: uuid.UUID | None = None
    lifecycle: Literal["active"] | None = None  # bring an archived device back


@router.patch("/devices/{device_id}")
async def patch_device(device_id: uuid.UUID, body: DevicePatch, db: DB, p: Admin) -> dict:
    d = await _device(db, device_id)
    fields = body.model_fields_set
    if "name" in fields and body.name:
        d.name = body.name.strip()
    if "room_id" in fields:
        if body.room_id is not None:
            room = await db.get(Room, body.room_id)
            if room is None or room.home_id != d.home_id:
                raise api_error(422, "validation_error", "Deze ruimte hoort niet bij deze woning.")
        d.room_id = body.room_id
    if "lifecycle" in fields and body.lifecycle == "active" and d.lifecycle == "archived":
        d.lifecycle = "active"
        d.diagnostics = {k: v for k, v in (d.diagnostics or {}).items() if k not in ("archived_by", "archived_at")}
        await audit(db, p.actor, "device_restored", d.name, device=str(d.id))
    await db.commit()
    return await device_view(db, d)


class CalibrationIn(BaseModel):
    temperature_offset: float | None = Field(default=None, ge=-10, le=10)
    humidity_offset: float | None = Field(default=None, ge=-30, le=30)


@router.patch("/devices/{device_id}/calibration")
async def calibrate(device_id: uuid.UUID, body: CalibrationIn, db: DB, p: Admin) -> dict:
    """Offsets added to new temperature/humidity readings (the raw value is kept with each reading)."""
    d = await _device(db, device_id)
    if d.kind not in CALIBRATABLE_KINDS:
        raise api_error(409, "unsupported", "Alleen sensoren en airco-units kun je kalibreren.")
    cal = dict(d.calibration or {})
    for f in body.model_fields_set:
        v = getattr(body, f)
        cal[f] = round(float(v), 2) if v is not None else 0.0
    cal["updated_at"] = datetime.now(UTC).isoformat()
    d.calibration = cal
    changed = {k: cal.get(k) for k in body.model_fields_set}
    await audit(db, p.actor, "device_calibrated", d.name, device=str(d.id), **changed)
    await db.commit()
    return await device_view(db, d)


# --- roles ---------------------------------------------------------------------------------------


class BindIn(BaseModel):
    role: str = Field(max_length=64)


def _check_role_fit(role: LogicalRole, d: Device) -> None:
    kinds = ROLE_KINDS.get(role.key)
    if kinds and d.kind not in kinds:
        wanted = " of ".join(sorted(KIND_LABELS.get(k, k) for k in kinds))
        raise api_error(
            409,
            "role_mismatch",
            f"De rol ‘{role.label}’ past niet bij dit apparaat ({KIND_LABELS.get(d.kind, d.kind)}); kies een {wanted}.",
        )


async def _role(db: DB, home_id: uuid.UUID, key: str) -> LogicalRole:
    role = (
        await db.execute(select(LogicalRole).where(LogicalRole.home_id == home_id, LogicalRole.key == key))
    ).scalar_one_or_none()
    if not role:
        raise not_found("Rol")
    return role


@router.post("/devices/{device_id}/bind")
async def bind(device_id: uuid.UUID, body: BindIn, db: DB, p: Admin) -> dict:
    """Bind a device to a logical role. The previous device keeps its history (binding closed, not deleted)."""
    d = await _device(db, device_id)
    if d.lifecycle == "archived":
        raise api_error(409, "archived", "Dit apparaat is gearchiveerd.")
    role = await _role(db, d.home_id, body.role)
    _check_role_fit(role, d)
    now = datetime.now(UTC)
    for b in (
        await db.execute(
            select(DeviceBinding).where(DeviceBinding.role_id == role.id, DeviceBinding.effective_to.is_(None))
        )
    ).scalars():
        if b.device_id == d.id:
            return await device_view(db, d)
        b.effective_to = now
        old = await db.get(Device, b.device_id)
        if old and old.id != d.id:
            old.lifecycle = "replaced"
    db.add(DeviceBinding(role_id=role.id, device_id=d.id, effective_from=now))
    await audit(db, p.actor, "role_bound", body.role, device=str(d.id))
    await db.commit()
    return await device_view(db, d)


@router.post("/devices/{device_id}/unbind")
async def unbind(device_id: uuid.UUID, body: BindIn, db: DB, p: Admin) -> dict:
    """End a role binding; the history up to now stays."""
    d = await _device(db, device_id)
    role = await _role(db, d.home_id, body.role)
    rows = (
        (
            await db.execute(
                select(DeviceBinding).where(
                    DeviceBinding.role_id == role.id,
                    DeviceBinding.device_id == d.id,
                    DeviceBinding.effective_to.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise api_error(409, "not_bound", "Dit apparaat heeft deze rol niet.")
    now = datetime.now(UTC)
    for b in rows:
        b.effective_to = now
    await audit(db, p.actor, "role_unbound", body.role, device=str(d.id))
    await db.commit()
    return await device_view(db, d)


# --- archive / replace ----------------------------------------------------------------------------


async def _close_bindings(db: DB, device_id: uuid.UUID, now: datetime) -> list[LogicalRole]:
    rows = (
        await db.execute(
            select(DeviceBinding, LogicalRole)
            .join(LogicalRole, LogicalRole.id == DeviceBinding.role_id)
            .where(DeviceBinding.device_id == device_id, DeviceBinding.effective_to.is_(None))
        )
    ).all()
    for r in rows:
        r.DeviceBinding.effective_to = now
    return [r.LogicalRole for r in rows]


def _reset_unit_tracking(z: ClimateZone) -> None:
    z.last_commanded, z.mismatch_count, z.correction_c = {}, 0, 0.0
    z.pending_command_id, z.pending_since = None, None
    if z.control in ("manual", "off"):
        z.control, z.override_until, z.override_source = "schedule", None, None


@router.delete("/devices/{device_id}")
async def archive_device(device_id: uuid.UUID, db: DB, p: Admin) -> dict:
    """Remove a device from use: archived, roles and zone links ended, queued commands cancelled.
    Its readings and history stay. PATCH {"lifecycle": "active"} brings it back."""
    d = await _device(db, device_id)
    now = datetime.now(UTC)
    roles = await _close_bindings(db, d.id, now)
    zones = 0
    for z in (
        await db.execute(
            select(ClimateZone).where((ClimateZone.unit_device_id == d.id) | (ClimateZone.sensor_device_id == d.id))
        )
    ).scalars():
        if z.unit_device_id == d.id:
            z.unit_device_id = None
            _reset_unit_tracking(z)
        if z.sensor_device_id == d.id:
            z.sensor_device_id = None
        db.add(ZoneLog(zone_id=z.id, kind="config", message=f"{p.user.username}: apparaat {d.name} gearchiveerd"))
        zones += 1
    await db.execute(
        update(CommandRecord)
        .where(CommandRecord.device_id == d.id, CommandRecord.status == "queued")
        .values(status="cancelled", detail="Apparaat gearchiveerd", updated_at=now)
    )
    await db.execute(update(ButtonMapping).where(ButtonMapping.device_id == d.id).values(enabled=False))
    d.lifecycle = "archived"
    d.online = False
    d.diagnostics = {**(d.diagnostics or {}), "archived_by": "user", "archived_at": now.isoformat()}
    await audit(db, p.actor, "device_archived", d.name, device=str(d.id), roles=[r.key for r in roles], zones=zones)
    await db.commit()
    return await device_view(db, d)


class ReplaceIn(BaseModel):
    new_device_id: uuid.UUID


def _swap_step_ids(action: dict, old: str, new: str) -> tuple[dict, bool]:
    raw = json.dumps(action)
    if old not in raw:
        return action, False
    return json.loads(raw.replace(old, new)), True


@router.post("/devices/{device_id}/replace")
async def replace_device(device_id: uuid.UUID, body: ReplaceIn, db: DB, p: Admin) -> dict:
    """Move role bindings, zone links and button mappings from this device to a new one. Both keep
    their own history; the old device becomes "replaced"."""
    old = await _device(db, device_id)
    new = await _device(db, body.new_device_id)
    if new.id == old.id or new.home_id != old.home_id:
        raise api_error(422, "validation_error", "Kies een ander apparaat in dezelfde woning.")
    if new.lifecycle == "archived":
        raise api_error(409, "archived", "Het nieuwe apparaat is gearchiveerd; zet het eerst terug.")
    now = datetime.now(UTC)
    roles = (
        (
            await db.execute(
                select(LogicalRole)
                .join(DeviceBinding, DeviceBinding.role_id == LogicalRole.id)
                .where(DeviceBinding.device_id == old.id, DeviceBinding.effective_to.is_(None))
            )
        )
        .scalars()
        .all()
    )
    zones = (
        (
            await db.execute(
                select(ClimateZone).where(
                    (ClimateZone.unit_device_id == old.id) | (ClimateZone.sensor_device_id == old.id)
                )
            )
        )
        .scalars()
        .all()
    )
    own_maps = (await db.execute(select(ButtonMapping).where(ButtonMapping.device_id == old.id))).scalars().all()
    # Check everything before changing anything.
    for role in roles:
        _check_role_fit(role, new)
    for z in zones:
        if z.unit_device_id == old.id and new.kind != "ac_unit":
            raise api_error(409, "role_mismatch", f"Zone {z.name} heeft een airco-unit nodig.")
        if z.sensor_device_id == old.id and new.kind not in SENSOR_KINDS:
            raise api_error(409, "role_mismatch", f"Zone {z.name} heeft een temperatuursensor nodig.")
    if own_maps and new.kind != "button":
        raise api_error(409, "role_mismatch", "Knopkoppelingen kunnen alleen naar een andere knop.")
    if not roles and not zones and not own_maps and new.kind != old.kind:
        raise api_error(409, "role_mismatch", "Het nieuwe apparaat is van een ander type.")
    for b in (
        await db.execute(
            select(DeviceBinding).where(DeviceBinding.device_id == old.id, DeviceBinding.effective_to.is_(None))
        )
    ).scalars():
        b.effective_to = now
        db.add(DeviceBinding(role_id=b.role_id, device_id=new.id, effective_from=now))
    for z in zones:
        if z.unit_device_id == old.id:
            z.unit_device_id = new.id
            _reset_unit_tracking(z)
        if z.sensor_device_id == old.id:
            z.sensor_device_id = new.id
        db.add(ZoneLog(zone_id=z.id, kind="config", message=f"{p.user.username}: {old.name} vervangen door {new.name}"))
    for m in own_maps:
        m.device_id = new.id
    steps_moved = 0
    for m in (await db.execute(select(ButtonMapping).where(ButtonMapping.home_id == old.home_id))).scalars():
        action, changed = _swap_step_ids(m.action or {}, str(old.id), str(new.id))
        if changed:
            m.action = action
            steps_moved += 1
    await db.execute(
        update(CommandRecord)
        .where(CommandRecord.device_id == old.id, CommandRecord.status == "queued")
        .values(status="cancelled", detail="Apparaat vervangen", updated_at=now)
    )
    old.lifecycle = "replaced"
    old.diagnostics = {**(old.diagnostics or {}), "replaced_by": str(new.id), "replaced_at": now.isoformat()}
    if old.room_id and not new.room_id:
        new.room_id = old.room_id
    moved = {"roles": sorted(r.key for r in roles), "zones": len(zones), "mappings": len(own_maps) + steps_moved}
    await audit(db, p.actor, "device_replaced", old.name, old=str(old.id), new=str(new.id), **moved)
    await db.commit()
    return {"old": await device_view(db, old), "new": await device_view(db, new), "moved": moved}


# --- test, tested, diagnostics --------------------------------------------------------------------


def _box() -> SecretBox:
    return SecretBox(get_settings().resolved_secret_key())


def _adapter_for(integ: Integration, creds: dict | None = None):
    info = providers().get(integ.provider)
    if info is None:
        return None
    kwargs = {}
    if integ.provider == "homewizard":
        kwargs["relay_allowed"] = lambda _identity: False  # never switch from the API
    try:
        return info.adapter(dict(integ.config or {}), dict(creds or {}), **kwargs)
    except TypeError:
        return info.adapter(dict(integ.config or {}), dict(creds or {}))


async def _close(adapter) -> None:
    close = getattr(adapter, "close", None)
    if callable(close):
        try:
            await close()
        except Exception as exc:  # closing must never hide the real answer
            log.debug("Adapter sluiten mislukt: %s", exc)


async def _latest(db: DB, d: Device) -> dict:
    out = {}
    for metric in SHOWN_METRICS:
        r = await latest_reading(db, d.id, metric)
        if r is not None:
            out[metric] = {"value": r.value, "unit": r.unit, "quality": r.quality, "at": r.observed_at, "raw": r.raw}
    return out


def _seen_check(d: Device, now: datetime) -> dict:
    if d.last_seen is None:
        return {"key": "seen", "label": "Laatste bericht", "state": "warn", "detail": "Nog nooit iets ontvangen"}
    age = (now - d.last_seen).total_seconds()
    mins = int(age // 60)
    when = "zojuist" if age < 90 else f"{mins} min geleden"
    state = "ok" if d.online and age < 3600 else ("warn" if age < 6 * 3600 else "error")
    detail = f"Laatste bericht {when}" + ("" if d.online else "; het apparaat staat als offline gemeld")
    return {"key": "seen", "label": "Laatste bericht", "state": state, "detail": detail}


@router.post("/devices/{device_id}/test")
async def test_device(device_id: uuid.UUID, db: DB, p: Admin) -> dict:
    """Non-destructive check: reads only (health probe of the integration, last messages, latest
    readings). Never switches anything — not the boiler, not a socket, not a lamp."""
    from ..worker.core import error_code, error_text

    d = await _device(db, device_id)
    now = datetime.now(UTC)
    checks = [_seen_check(d, now)]
    integ = await db.get(Integration, d.integration_id) if d.integration_id else None
    if integ is None:
        checks.append(
            {"key": "connection", "label": "Verbinding", "state": "warn", "detail": "Geen koppeling bij dit apparaat"}
        )
    elif not integ.enabled:
        checks.append({"key": "connection", "label": "Verbinding", "state": "warn", "detail": "Koppeling staat uit"})
    elif integ.provider == "zigbee":
        det = integration_details(integ)
        extra = det["extra"]
        state = integ.state if integ.state in ("ok", "warn", "error") else "unknown"
        checks.append({"key": "connection", "label": "Zigbee", "state": state, "detail": integ.detail or ""})
        if extra:
            mqtt = extra.get("mqtt_connected")
            bridge = extra.get("bridge_online")
            checks.append(
                {
                    "key": "mqtt",
                    "label": "MQTT-verbinding",
                    "state": "ok" if mqtt else ("error" if mqtt is False else "unknown"),
                    "detail": "Verbonden" if mqtt else (extra.get("mqtt_error") or "Niet verbonden"),
                }
            )
            checks.append(
                {
                    "key": "dongle",
                    "label": "USB-dongle",
                    "state": "ok" if bridge else ("error" if bridge is False else "unknown"),
                    "detail": "Zigbee2MQTT online" if bridge else "Zigbee2MQTT niet online",
                }
            )
    else:
        try:
            creds = _box().decrypt(integ.credentials_enc) if integ.credentials_enc else {}
        except Exception:
            creds = None
        if creds is None:
            checks.append(
                {
                    "key": "connection",
                    "label": "Verbinding",
                    "state": "error",
                    "detail": "Sleutel past niet, koppel opnieuw.",
                    "code": "key_mismatch",
                }
            )
        else:
            adapter = _adapter_for(integ, creds)
            try:
                h = await asyncio.wait_for(adapter.health(), timeout=PROBE_TIMEOUT_S)
                state = str(getattr(h.state, "value", h.state))
                checks.append(
                    {
                        "key": "connection",
                        "label": "Verbinding",
                        "state": "warn" if state == "not_configured" else state,
                        "detail": h.detail or "",
                        "code": getattr(h, "code", None),
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "key": "connection",
                        "label": "Verbinding",
                        "state": "error",
                        "detail": error_text(exc),
                        "code": error_code(exc),
                    }
                )
            finally:
                await _close(adapter)
    readings = await _latest(db, d)
    if readings:
        parts = [f"{m} {v['value']} {v['unit']}" for m, v in readings.items() if v["value"] is not None][:6]
        checks.append({"key": "readings", "label": "Laatste metingen", "state": "ok", "detail": ", ".join(parts)})
    relay = _details(d).get("relay")
    if relay:
        on = relay.get("on")
        lock = relay.get("switch_lock")
        checks.append(
            {
                "key": "relay",
                "label": "Stekker",
                "state": "unknown" if relay.get("state") == "unsupported" else ("ok" if on else "warn"),
                "detail": "Stand niet uit te lezen"
                if relay.get("state") == "unsupported"
                else f"{'aan' if on else 'uit' if on is False else 'onbekend'}; switch lock "
                f"{'aan' if lock else 'uit' if lock is False else 'onbekend'} (alleen uitgelezen)",
            }
        )
    ok = not any(c["state"] == "error" for c in checks)
    result = {
        "device_id": d.id,
        "ok": ok,
        "checked_at": now,
        "checks": checks,
        "readings": readings,
        "note": "Er is niets geschakeld; alleen uitgelezen.",
    }
    d.diagnostics = {
        **(d.diagnostics or {}),
        "last_test": json.loads(json.dumps({k: v for k, v in result.items() if k != "readings"}, default=str)),
    }
    await audit(db, p.actor, "device_tested_probe", d.name, device=str(d.id), ok=ok)
    await db.commit()
    return result


class TestedIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/devices/{device_id}/tested")
async def mark_tested(device_id: uuid.UUID, db: DB, p: Admin, body: TestedIn | None = None) -> dict:
    """Manual confirmation that this device works in this home ("getest")."""
    d = await _device(db, device_id)
    now = datetime.now(UTC)
    d.tested_at = now
    d.compatibility = "tested"
    d.diagnostics = {
        **(d.diagnostics or {}),
        "tested": {"at": now.isoformat(), "by": p.user.username, "note": body.note if body else None},
    }
    await audit(db, p.actor, "device_marked_tested", d.name, device=str(d.id), model=d.model, firmware=d.firmware)
    await db.commit()
    return await device_view(db, d)


def _integration_blob(i: Integration) -> dict:
    return {
        "id": i.id,
        "provider": i.provider,
        "name": i.name,
        "enabled": i.enabled,
        "identity": i.identity,
        "state": i.state,
        "detail": i.detail,
        "last_ok_at": i.last_ok_at,
        "config": i.config or {},  # never holds secrets; credentials are not included
        "diagnostics": i.diagnostics or {},
    }


@router.get("/devices/{device_id}/diagnostics")
async def device_diagnostics(device_id: uuid.UUID, db: DB, p: Admin) -> dict:
    """Everything useful for support, without secrets, plus a ready-to-copy text version."""
    d = await _device(db, device_id)
    integ = await db.get(Integration, d.integration_id) if d.integration_id else None
    cmds = (
        await db.execute(
            select(CommandRecord)
            .where(CommandRecord.device_id == d.id)
            .order_by(CommandRecord.created_at.desc())
            .limit(10)
        )
    ).scalars()
    blob = {
        "generated_at": datetime.now(UTC),
        "app_version": VERSION,
        "device": {
            "id": d.id,
            "provider": d.provider,
            "kind": d.kind,
            "name": d.name,
            "stable_identity": d.stable_identity,
            "model": d.model,
            "firmware": d.firmware,
            "compatibility": d.compatibility,
            "tested_at": d.tested_at,
            "lifecycle": d.lifecycle,
            "online": d.online,
            "last_seen": d.last_seen,
            "capabilities": d.capabilities or [],
            "calibration": d.calibration or {},
            "state": d.state or {},
            "diagnostics": d.diagnostics or {},
        },
        "integration": _integration_blob(integ) if integ else None,
        "latest_readings": await _latest(db, d),
        "recent_commands": [
            {"action": c.action, "params": c.params, "status": c.status, "detail": c.detail, "at": c.created_at}
            for c in cmds
        ],
    }
    clean = json.loads(json.dumps(blob, default=str))
    return {"diagnostics": clean, "text": json.dumps(clean, indent=2, ensure_ascii=False)}


# --- integrations ----------------------------------------------------------------------------------


def integration_view(i: Integration, detailed: bool = False) -> dict:
    det = integration_details(i)
    info = providers().get(i.provider)
    v = {
        "id": i.id,
        "provider": i.provider,
        "label": i.name,
        "provider_label": getattr(info, "label", i.provider),
        "home_id": i.home_id,
        "enabled": i.enabled,
        "identity": i.identity,
        "host": (i.config or {}).get("host"),
        "state": i.state,
        "detail": i.detail,
        "last_ok_at": i.last_ok_at,
        "code": det["code"],
        "connected_since": det["connected_since"],
        "missed_polls": det["missed_polls"],
        "checked_at": det["checked_at"],
        "has_diagnostics": bool((i.diagnostics or {}).get("pair")),
    }
    if detailed:
        v["health"] = {k: det[k] for k in ("health_state", "health_detail", "checked_at", "extra")}
        v["config"] = i.config or {}
        v["diagnostics"] = i.diagnostics or {}
    return v


@router.get("/integrations")
async def integrations(db: DB, p: Auth, home_id: uuid.UUID | None = None) -> list[dict]:
    q = select(Integration).order_by(Integration.provider, Integration.name)
    if home_id:
        q = q.where(Integration.home_id == home_id)
    rows = (await db.execute(q)).scalars().all()
    configured = {i.provider for i in rows if i.enabled}
    out = [integration_view(i) for i in rows]
    for name, info in providers().items():
        if name not in configured:
            out.append(
                {
                    "id": None,
                    "provider": name,
                    "label": info.label,
                    "provider_label": info.label,
                    "home_id": home_id,
                    "enabled": False,
                    "state": "not_configured",
                    "detail": info.needs_manufacturer_app or "",
                    "last_ok_at": None,
                }
            )
    return out


# Support matrix (docs/compatibility.md as data). "match": model ids that belong to the row.
COMPATIBILITY_ROWS = [
    {
        "provider": "hue",
        "model": "Bridge v2 (BSB002)",
        "protocol": "CLIP API v2 (HTTPS, eventstream)",
        "local": "Lampen, groepen (aan/totaal, actieve scène, bereikbaarheid), scènes, knoppen",
        "vendor_app": "Lampen en knoppen eerst in de Hue-app aan de bridge toevoegen",
        "status": "untested",
        "kinds": ["hue_bridge", "light", "light_group", "scene", "button"],
        "match": ["BSB002"],
    },
    {
        "provider": "hue",
        "model": "Bridge v1 (rond)",
        "protocol": "—",
        "local": "Niet ondersteund (geen API v2)",
        "vendor_app": None,
        "status": "unsupported",
        "kinds": [],
        "match": ["BSB001"],
    },
    {
        "provider": "homewizard",
        "model": "P1-meter (HWE-P1)",
        "protocol": "v1 (HTTP) en v2 (HTTPS + token)",
        "local": "Vermogen, kWh import/export per tarief, gas",
        "vendor_app": "‘Lokale API’ aan in de HomeWizard Energy-app",
        "status": "untested",
        "kinds": ["p1_meter"],
        "match": ["HWE-P1"],
    },
    {
        "provider": "homewizard",
        "model": "Energy Socket (HWE-SKT)",
        "protocol": "v1 (HTTP)",
        "local": "Vermogen, kWh, stand van de stekker en ‘Switch lock’ (alleen uitlezen; boiler nooit schakelen)",
        "vendor_app": "‘Lokale API’ aan in de HomeWizard Energy-app",
        "status": "untested",
        "kinds": ["energy_socket"],
        "match": ["HWE-SKT"],
    },
    {
        "provider": "homewizard",
        "model": "kWh-meter 1-fase (HWE-KWH1)",
        "protocol": "v1 en v2",
        "local": "Vermogen, kWh import/export",
        "vendor_app": "‘Lokale API’ aan in de HomeWizard Energy-app",
        "status": "untested",
        "kinds": ["kwh_meter"],
        "match": ["HWE-KWH1"],
    },
    {
        "provider": "homewizard",
        "model": "kWh-meter 3-fase (HWE-KWH3), SDM230-wifi, SDM630-wifi",
        "protocol": "v1 (SDM) / v1+v2 (KWH3)",
        "local": "Vermogen, kWh",
        "vendor_app": "‘Lokale API’ aan in de HomeWizard Energy-app",
        "status": "untested",
        "kinds": ["kwh_meter"],
        "match": ["HWE-KWH3", "SDM230-wifi", "SDM630-wifi"],
    },
    {
        "provider": "gree",
        "model": "Multisplit binnenunits",
        "protocol": "Lokaal UDP-protocol (poort 7000), AES-ECB (v1) of AES-GCM (v2)",
        "local": "Aan/uit, modus, doeltemperatuur (hele graden), ventilator en swing als de unit ze heeft",
        "vendor_app": "Wifi-koppeling via de Gree+-app (2,4 GHz)",
        "status": "untested",
        "kinds": ["ac_unit"],
        "match": [],
    },
    {
        "provider": "zigbee",
        "model": "Aqara WSDCGQ11LM (temperatuur/vocht/druk)",
        "protocol": "Zigbee2MQTT via MQTT",
        "local": "Temperatuur, vochtigheid, druk, batterij, linkkwaliteit",
        "vendor_app": None,
        "status": "untested",
        "kinds": ["zigbee_sensor"],
        "match": ["WSDCGQ11LM"],
    },
    {
        "provider": "zigbee",
        "model": "Coördinator: SONOFF ZBDongle-E (ember) of ZBDongle-P (zstack)",
        "protocol": "Zigbee2MQTT",
        "local": "Status: MQTT-verbinding en Zigbee2MQTT online/offline apart",
        "vendor_app": None,
        "status": "untested",
        "kinds": ["zigbee_bridge"],
        "match": [],
    },
]


@router.get("/integrations/compatibility")
async def compatibility(db: DB, p: Auth) -> dict:
    """The support matrix as data, with the devices in this installation per row and their status."""
    devs = (await db.execute(select(Device).where(Device.lifecycle != "archived"))).scalars().all()
    rows = []
    used: set[uuid.UUID] = set()
    extra_rows = []
    for info in providers().values():
        extra_rows += list(getattr(info, "compatibility", None) or [])
    for row in [*COMPATIBILITY_ROWS, *extra_rows]:
        match = {m.lower() for m in row.get("match", [])}
        kinds = set(row.get("kinds", []))
        mine = [
            d
            for d in devs
            if d.provider == row["provider"]
            and d.id not in used
            and ((match and (d.model or "").lower() in match) or (not match and d.kind in kinds))
        ]
        used.update(d.id for d in mine)
        counts = {"tested": 0, "untested": 0, "unsupported": 0}
        for d in mine:
            counts[d.compatibility if d.compatibility in counts else "untested"] += 1
        status = row["status"]
        if counts["unsupported"] and not counts["tested"]:
            status = "unsupported"
        elif counts["tested"]:
            status = "tested"
        rows.append(
            {
                **{k: v for k, v in row.items() if k not in ("match", "kinds")},
                "status": status,
                "devices": [
                    {
                        "id": d.id,
                        "name": d.name,
                        "model": d.model,
                        "compatibility": d.compatibility,
                        "tested_at": d.tested_at,
                    }
                    for d in mine
                ],
                "counts": counts,
            }
        )
    return {
        "providers": [
            {
                "provider": name,
                "label": info.label,
                "works_locally": info.works_locally,
                "needs_manufacturer_app": info.needs_manufacturer_app,
            }
            for name, info in providers().items()
        ],
        "rows": rows,
        "note": "‘Getest’ betekent: in dit huis met een echt apparaat gecontroleerd (knop ‘Getest’ bij het apparaat).",
    }


async def _integration(db: DB, integration_id: uuid.UUID) -> Integration:
    i = await db.get(Integration, integration_id)
    if not i:
        raise not_found("Integratie")
    return i


@router.get("/integrations/{integration_id}")
async def integration_detail(integration_id: uuid.UUID, db: DB, p: Admin) -> dict:
    i = await _integration(db, integration_id)
    v = integration_view(i, detailed=True)
    v["devices"] = [
        {"id": d.id, "name": d.name, "kind": d.kind, "online": d.online, "lifecycle": d.lifecycle}
        for d in (
            await db.execute(select(Device).where(Device.integration_id == i.id).order_by(Device.kind, Device.name))
        ).scalars()
    ]
    return v


@router.get("/integrations/{provider}/instructions")
async def instructions(provider: str, p: Auth) -> dict:
    info = providers().get(provider)
    if not info:
        raise not_found("Integratie")
    return {
        "provider": provider,
        "label": info.label,
        "steps": info.instructions.steps,
        "needs_manufacturer_app": info.needs_manufacturer_app,
        "works_locally": info.works_locally,
    }


# --- addresses, errors ----------------------------------------------------------------------------


def _allowed_hosts() -> set[str]:
    return get_settings().pairing_allowed_host_set()  # SHC_PAIRING_ALLOWED_HOSTS


def validate_host(v: str) -> str:
    """A device address in the home network: private IP or LAN name. Raises ValueError (Dutch)."""
    v = v.strip()
    if v.lower() in _allowed_hosts():
        return v
    try:
        ip = ipaddress.ip_address(v.strip("[]"))
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
            raise ValueError(
                "Dit adres is niet toegestaan (loopback of link-local). Gebruik het IP-adres van het apparaat "
                "in je thuisnetwerk, bijvoorbeeld 192.168.1.50."
            )
        if not ip.is_private:
            raise ValueError("Alleen apparaten in het lokale netwerk zijn toegestaan.")
        if any(ip in net for net in get_settings().stack_networks()):
            raise ValueError(
                "Dit adres hoort bij de app zelf (database, MQTT, web), niet bij een apparaat. Gebruik het "
                "IP-adres van het apparaat in je thuisnetwerk, bijvoorbeeld 192.168.1.50."
            )
        return str(ip)
    name = v.lower().rstrip(".")
    if not _NAME_RE.match(name):
        raise ValueError("Ongeldig adres")
    if name == "localhost" or name.endswith(".localhost"):
        raise ValueError("Dit adres is niet toegestaan (loopback). Gebruik het IP-adres van het apparaat.")
    if "." not in name or not name.endswith(LAN_SUFFIXES):
        raise ValueError(
            "Gebruik het IP-adres van het apparaat, of een naam in je thuisnetwerk die eindigt op .local of .lan."
        )
    return name


def describe_error(exc: BaseException, host: str | None = None) -> tuple[int, str, str]:
    """(HTTP status, code, Dutch message) for a discovery/pairing/probe failure. Adapter errors carry
    their own Dutch text; anything else is mapped, never echoed (no raw exception text)."""
    import httpx
    from shc_integrations.base import AdapterError

    where = f" op {host}" if host else ""
    if isinstance(exc, AdapterError):
        code = exc.code
        status = 409 if code in ("identity_mismatch", "certificate_mismatch") else 502
        return status, code, str(exc) or "Het apparaat gaf een fout."
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return 504, "device_offline", f"Het apparaat{where} antwoordt niet. Controleer het adres en of het aan staat."
    if isinstance(exc, httpx.HTTPStatusError):
        return (
            502,
            "pair_failed",
            f"Het apparaat{where} gaf een foutmelding (HTTP {exc.response.status_code}). Staat de lokale API aan?",
        )
    if isinstance(exc, httpx.TransportError | OSError):
        return 502, "device_offline", f"Geen verbinding met het apparaat{where}. Controleer het adres."
    if isinstance(exc, ValueError | KeyError | TypeError):
        return 502, "pair_failed", f"Onverwacht antwoord van het apparaat{where}. Is dit het juiste apparaat?"
    return 502, "pair_failed", "Koppelen mislukt door een onverwachte fout. Controleer het adres en probeer opnieuw."


class DiscoverIn(BaseModel):
    home_id: uuid.UUID
    timeout_s: float = Field(default=5, ge=1, le=30)


def _disc_view(x: Any) -> dict:
    return {
        "provider": x.provider,
        "stable_identity": x.stable_identity,
        "kind": x.kind,
        "name": x.name,
        "host": x.host,
        "model": x.model,
        "firmware": x.firmware,
    }


@router.post("/integrations/{provider}/discover")
async def discover(provider: str, body: DiscoverIn, p: Admin) -> list[dict]:
    info = providers().get(provider)
    if not info:
        raise not_found("Integratie")
    adapter = info.adapter({}, {})
    try:
        found = await adapter.discover(body.timeout_s)
    except Exception as exc:
        log.info("Zoeken naar %s mislukt: %r", provider, exc)
        _status, code, msg = describe_error(exc)
        raise api_error(502, "discovery_failed", f"Zoeken mislukt: {msg} Voer het IP-adres handmatig in.") from exc
    finally:
        await _close(adapter)
    return [_disc_view(x) for x in found]


class PairIn(BaseModel):
    home_id: uuid.UUID
    host: str = Field(max_length=255)
    name: str | None = Field(default=None, max_length=100)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        return validate_host(v)

    @field_validator("options")
    @classmethod
    def _options(cls, v: dict[str, Any]) -> dict[str, Any]:
        # The adapter config is {"host": host, **options}: an address in the options would skip the
        # check above (stack subnets, loopback, public addresses).
        if "host" in v:
            raise ValueError("Geef het adres op in het veld 'host', niet in de opties.")
        return v


def _identity_from_diagnostics(provider: str, diag: dict) -> str | None:
    if provider == "gree":
        scan = diag.get("scan") if isinstance(diag.get("scan"), dict) else {}
        mac = diag.get("mac") or scan.get("mac")
        return str(mac)[:128] if mac else None
    return None


async def _record_pair_failure(
    db: DB, home: Home, provider: str, body: PairIn, exc: BaseException, adapter, code: str, message: str, actor: str
) -> dict:
    """Keep what we learned from a failed pairing (verbeterlijst 29): diagnostics on an integration
    record (disabled when new), and for an unsupported unit a device marked "unsupported"."""
    diag = dict(getattr(exc, "diagnostics", None) or {})
    if not diag:
        try:
            fn = getattr(adapter, "diagnostics", None)
            diag = dict(fn() or {}) if callable(fn) else {}
        except Exception:
            diag = {}
    diag = json.loads(json.dumps(diag, default=str))
    identity = _identity_from_diagnostics(provider, diag)
    now = datetime.now(UTC)
    integ = None
    if identity:
        integ = (
            await db.execute(
                select(Integration).where(
                    Integration.home_id == home.id, Integration.provider == provider, Integration.identity == identity
                )
            )
        ).scalar_one_or_none()
    if integ is None:
        integ = (
            (
                await db.execute(
                    select(Integration).where(
                        Integration.home_id == home.id,
                        Integration.provider == provider,
                        Integration.enabled.is_(False),
                        Integration.identity.is_(None),
                        Integration.config["host"].astext == body.host,
                    )
                )
            )
            .scalars()
            .first()
        )
    info = providers().get(provider)
    if integ is None:
        integ = Integration(
            home_id=home.id,
            provider=provider,
            name=body.name or f"{getattr(info, 'label', provider)} {body.host}",
            config={"host": body.host},
            enabled=False,
            identity=identity,
            diagnostics={},
        )
        db.add(integ)
    if not integ.enabled:
        integ.state, integ.detail = "error", message[:500]
        if identity and integ.identity is None:
            integ.identity = identity
    pair_info = {"at": now.isoformat(), "host": body.host, "result": "failed", "code": code, "message": message}
    integ.diagnostics = {**(integ.diagnostics or {}), "pair": {**pair_info, "diagnostics": diag}}
    await db.flush()
    device_id = None
    if code == "unsupported" and identity:
        d = (
            await db.execute(
                select(Device).where(
                    Device.home_id == home.id, Device.provider == provider, Device.stable_identity == identity
                )
            )
        ).scalar_one_or_none()
        scan = diag.get("scan") if isinstance(diag.get("scan"), dict) else {}
        if d is None:
            d = Device(
                home_id=home.id,
                provider=provider,
                kind=DEFAULT_KIND.get(provider, "device"),
                name=body.name or scan.get("name") or f"{getattr(info, 'label', provider)} {identity[-6:]}",
                stable_identity=identity,
                integration_id=integ.id,
                state={},
                capabilities=[],
                calibration={},
            )
            db.add(d)
        if d.compatibility != "tested":
            d.compatibility = "unsupported"
        model = diag.get("model") or scan.get("model") or d.model
        d.model = str(model)[:100] if model else None
        fw = diag.get("firmware") or scan.get("ver")
        if fw:
            d.firmware = str(fw)[:64]
        d.diagnostics = {**(d.diagnostics or {}), "pair": {**pair_info, "diagnostics": diag}}
        await db.flush()
        device_id = d.id
    await audit(db, actor, "integration_pair_failed", provider, host=body.host, code=code)
    await db.commit()
    return {"integration_id": str(integ.id), "device_id": str(device_id) if device_id else None, "diagnostics": diag}


# Credential/config keys in which adapters keep the paired device's own identity (Hue: bridge_id).
IDENTITY_KEYS = ("bridge_id", "serial", "mac", "identity")


def _ident_norm(v: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(v).lower())


def legacy_same_device(integ: Integration, identity: str, host: str, box: SecretBox) -> bool:
    """Is this integration without a stored identity (paired before migration 0002) the device that
    is being paired now? Yes when its credentials or config name the same identity (Hue keeps the
    bridge id in its credentials); when they name none, when it used the same address."""
    try:
        creds = box.decrypt(integ.credentials_enc) if integ.credentials_enc else {}
    except Exception:
        creds = {}  # other master key: cannot tell from the credentials
    cfg = integ.config or {}
    want = _ident_norm(identity)
    named = False
    for src in (creds, cfg):
        for k in IDENTITY_KEYS:
            v = src.get(k) if isinstance(src, dict) else None
            if v:
                if _ident_norm(v) == want:
                    return True
                named = True
    if named:
        return False  # it knows its own device, and that is another one
    return bool(host) and str(cfg.get("host") or "").strip().lower() == host.strip().lower()


@router.post("/integrations/{provider}/pair")
async def pair(provider: str, body: PairIn, db: DB, p: Admin):
    """Pair a device. Pairing the same physical device again (same identity: serial, MAC, bridge id)
    updates its existing integration instead of adding a second one, and moves its devices there."""
    from shc_integrations.base import PairingInstructions, should_upgrade_kind

    info = providers().get(provider)
    if not info:
        raise not_found("Integratie")
    home = await get_home(db, body.home_id)
    if home.active_to is not None:
        raise api_error(409, "archived", "Deze woning is gearchiveerd.")
    adapter = info.adapter({"host": body.host, **body.options}, {})
    try:
        result = await adapter.pair(body.host, **body.options)
    except Exception as exc:
        status, code, message = describe_error(exc, body.host)
        log.info("Koppelen %s op %s mislukt: %r", provider, body.host, exc)
        saved = await _record_pair_failure(db, home, provider, body, exc, adapter, code, message, p.actor)
        raise HTTPException(
            status_code=status, detail={"code": code, "message": f"Koppelen mislukt: {message}", **saved}
        ) from exc
    finally:
        await _close(adapter)
    if isinstance(result, PairingInstructions):
        return JSONResponse(
            status_code=202,
            content={"status": "action_required", "steps": result.steps, "retry_after_s": result.retry_after_s},
        )
    box = SecretBox(get_settings().resolved_secret_key())
    dev = result.device
    extra = dict(dev.extra or {})
    identity = str(result.stable_identity)[:128]
    # The adapter fills in what it learned (serial, product type, API version, MAC, key version).
    config = {
        "host": body.host,
        **body.options,
        **getattr(adapter, "config", {}),
        **(extra.get("config", {}) if isinstance(extra.get("config"), dict) else {}),
    }
    now = datetime.now(UTC)
    same = list(
        (
            await db.execute(
                select(Integration)
                .where(
                    Integration.home_id == home.id, Integration.provider == provider, Integration.identity == identity
                )
                .order_by(Integration.enabled.desc(), Integration.created_at)
            )
        ).scalars()
    )
    legacy_ids = {
        r
        for r in (
            await db.execute(
                select(Device.integration_id).where(
                    Device.home_id == home.id,
                    Device.provider == provider,
                    Device.stable_identity == identity,
                    Device.integration_id.is_not(None),
                )
            )
        ).scalars()
    }
    for iid in legacy_ids:
        if all(i.id != iid for i in same):
            legacy = await db.get(Integration, iid)
            if legacy is not None and legacy.provider == provider and legacy.home_id == home.id:
                same.append(legacy)
    # Integrations from before stable identities (identity NULL), e.g. an older Hue integration that
    # still holds the lights and buttons while a newer one holds the bridge device (item 24).
    unidentified = (
        await db.execute(
            select(Integration)
            .where(Integration.home_id == home.id, Integration.provider == provider, Integration.identity.is_(None))
            .order_by(Integration.enabled.desc(), Integration.created_at)
        )
    ).scalars()
    for legacy in unidentified:
        if all(i.id != legacy.id for i in same) and legacy_same_device(legacy, identity, body.host, box):
            same.append(legacy)
    keep = same[0] if same else None
    others = same[1:]
    for o in others:
        o.identity = None
        o.enabled = False
        o.credentials_enc = None
        o.state, o.detail = "not_configured", "Vervangen door een nieuwere koppeling van hetzelfde apparaat"
    await db.flush()
    repaired = keep is not None
    if keep is None:
        keep = Integration(home_id=home.id, provider=provider, name=body.name or dev.name, diagnostics={})
        db.add(keep)
    elif body.name:
        keep.name = body.name
    keep.config = config
    keep.credentials_enc = box.encrypt(result.credentials) if result.credentials else None
    keep.enabled = True
    keep.identity = identity
    keep.state = "warn"
    keep.detail = "Opnieuw gekoppeld; wacht op eerste meting." if repaired else "Gekoppeld; wacht op eerste meting."
    keep.diagnostics = {
        **(keep.diagnostics or {}),
        "pair": {
            "at": now.isoformat(),
            "host": body.host,
            "result": "ok",
            "diagnostics": json.loads(json.dumps(extra.get("pair_diagnostics") or {}, default=str)),
        },
    }
    await db.flush()
    if others:
        await db.execute(
            update(Device).where(Device.integration_id.in_([o.id for o in others])).values(integration_id=keep.id)
        )
    d = (
        await db.execute(
            select(Device).where(
                Device.home_id == home.id, Device.provider == provider, Device.stable_identity == identity
            )
        )
    ).scalar_one_or_none()
    if d is None:
        d = Device(
            home_id=home.id,
            provider=provider,
            kind=dev.kind,
            name=body.name or dev.name,
            stable_identity=identity,
            state={},
            capabilities=[],
            calibration={},
            diagnostics={},
        )
        db.add(d)
    else:
        if should_upgrade_kind(d.kind, dev.kind):
            d.kind = dev.kind
        if body.name:
            d.name = body.name
        d.diagnostics = {k: v for k, v in (d.diagnostics or {}).items() if k not in ("archived_by", "archived_at")}
    d.integration_id, d.lifecycle = keep.id, "active"
    model, firmware = dev.model or d.model, dev.firmware or d.firmware
    d.model = str(model)[:100] if model else None
    d.firmware = str(firmware)[:64] if firmware else None
    if d.compatibility == "unsupported" or not d.compatibility:
        d.compatibility = "untested"
    caps = extra.get("caps")
    if isinstance(caps, dict):
        d.state = {**(d.state or {}), "caps": caps}
        if d.kind == "ac_unit":
            d.capabilities = ["power", "hvac_mode", "setpoint", *(c for c in ("fan", "swing") if caps.get(c))]
    if extra.get("pair_diagnostics"):
        d.diagnostics = {
            **(d.diagnostics or {}),
            "pair": {"at": now.isoformat(), "result": "ok", "diagnostics": extra["pair_diagnostics"]},
        }
    d.diagnostics = json.loads(json.dumps(d.diagnostics or {}, default=str))
    await notify_changed(db, keep)
    await audit(
        db,
        p.actor,
        "integration_paired",
        provider,
        host=body.host,
        integration=str(keep.id),
        repaired=repaired,
        replaced=[str(o.id) for o in others],
    )
    await db.commit()
    v = await device_view(db, d)
    v["integration_id"] = keep.id
    v["repaired"] = repaired
    return v


class IntegrationPatch(BaseModel):
    host: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    verify: bool = True

    @field_validator("host")
    @classmethod
    def _host(cls, v: str | None) -> str | None:
        return validate_host(v) if v is not None else None


def _expected_identity(i: Integration) -> str | None:
    c = i.config or {}
    return i.identity or c.get("serial") or c.get("mac") or c.get("bridge_id")


@router.patch("/integrations/{integration_id}")
async def patch_integration(integration_id: uuid.UUID, body: IntegrationPatch, db: DB, p: Admin) -> dict:
    """Change only the address (e.g. after a DHCP change) and/or the name. With ``verify`` (default)
    the device at the new address must be the same physical device (serial / MAC / bridge id)."""
    i = await _integration(db, integration_id)
    if body.name:
        i.name = body.name
    if body.host is not None and body.host != (i.config or {}).get("host"):
        if i.provider == "zigbee":
            raise api_error(409, "unsupported", "Het adres van Zigbee2MQTT stel je in bij de installatie.")
        found_identity = None
        if body.verify:
            info = providers().get(i.provider)
            if info is None:
                raise not_found("Integratie")
            adapter = info.adapter({**(i.config or {}), "host": body.host}, {})
            try:
                found = await asyncio.wait_for(adapter.probe(body.host), timeout=PROBE_TIMEOUT_S)
            except Exception as exc:
                _status, code, msg = describe_error(exc, body.host)
                raise api_error(
                    502,
                    code,
                    f"{msg} Het adres is niet gewijzigd. Staat het apparaat uit? Sla dan op zonder controle.",
                ) from exc
            finally:
                await _close(adapter)
            found_identity = str(found.stable_identity)
            expected = _expected_identity(i)
            if expected and found_identity.lower() != str(expected).lower():
                raise api_error(
                    409,
                    "identity_mismatch",
                    f"Op {body.host} antwoordt een ander apparaat ({found_identity}, verwacht {expected}). "
                    "Het adres is niet gewijzigd.",
                )
        old = (i.config or {}).get("host")
        i.config = {**(i.config or {}), "host": body.host}
        if found_identity and not i.identity:
            clash = (
                await db.execute(
                    select(Integration.id).where(
                        Integration.home_id == i.home_id,
                        Integration.provider == i.provider,
                        Integration.identity == found_identity,
                        Integration.id != i.id,
                    )
                )
            ).first()
            if clash is None:
                i.identity = found_identity[:128]
        i.detail = f"Adres gewijzigd naar {body.host}; wordt opnieuw verbonden."
        await audit(db, p.actor, "integration_address", i.provider, integration=str(i.id), old=old, new=body.host)
        await notify_changed(db, i)
    await db.commit()
    return integration_view(i, detailed=True)


@router.delete("/integrations/{integration_id}", status_code=204)
async def remove_integration(integration_id: uuid.UUID, db: DB, p: Admin):
    i = await _integration(db, integration_id)
    i.enabled = False
    i.credentials_enc = None
    i.state, i.detail = "not_configured", "Ontkoppeld"
    await audit(db, p.actor, "integration_removed", i.provider)
    await notify_changed(db, i)
    await db.commit()
    return JSONResponse(status_code=204, content=None)


# --- Zigbee ---------------------------------------------------------------------------------------


class ZigbeeSetup(BaseModel):
    home_id: uuid.UUID


@router.post("/integrations/zigbee/setup")
async def zigbee_setup(body: ZigbeeSetup, db: DB, p: Admin) -> dict:
    """Register the internal Zigbee2MQTT bridge (no network call: the worker connects to MQTT)."""
    home = await get_home(db, body.home_id)
    integ = (
        (
            await db.execute(
                select(Integration)
                .where(Integration.home_id == home.id, Integration.provider == "zigbee")
                .order_by(Integration.enabled.desc(), Integration.created_at)
            )
        )
        .scalars()
        .first()
    )
    if integ is None:
        s = get_settings()
        integ = Integration(
            home_id=home.id,
            provider="zigbee",
            name="Zigbee",
            config={"host": s.mqtt_host, "port": s.mqtt_port, "username": s.mqtt_username or "shc"},
            state="warn",
            detail="Wacht op verbinding met Zigbee2MQTT.",
            diagnostics={},
        )
        db.add(integ)
        await db.flush()
    integ.enabled = True
    bridge = (
        (
            await db.execute(
                select(Device).where(
                    Device.home_id == home.id,
                    Device.provider == "zigbee",
                    (Device.kind == "zigbee_bridge") | (Device.stable_identity == "zigbee2mqtt-bridge"),
                )
            )
        )
        .scalars()
        .first()
    )
    if bridge is None:
        bridge = Device(
            home_id=home.id,
            integration_id=integ.id,
            provider="zigbee",
            kind="zigbee_bridge",
            name="Zigbee-coördinator",
            stable_identity="zigbee2mqtt-bridge",
            compatibility="untested",
            state={},
            capabilities=[],
            calibration={},
            diagnostics={},
        )
        db.add(bridge)
    else:
        bridge.integration_id, bridge.kind = integ.id, "zigbee_bridge"
    await audit(db, p.actor, "zigbee_setup")
    await notify_changed(db, integ)
    await db.commit()
    return await device_view(db, bridge)


class PermitJoin(BaseModel):
    home_id: uuid.UUID
    seconds: int = Field(default=120, ge=30, le=254)


@router.post("/integrations/zigbee/permit-join", status_code=202)
async def zigbee_permit_join(body: PermitJoin, db: DB, p: Admin):
    from ..commanding import queue_command

    bridge = (
        (await db.execute(select(Device).where(Device.home_id == body.home_id, Device.kind == "zigbee_bridge")))
        .scalars()
        .first()
    )
    if bridge is None:
        raise api_error(409, "not_configured", "Zigbee is nog niet ingesteld.")
    cmd = await queue_command(db, body.home_id, bridge, "permit_join", {"seconds": body.seconds}, "user", p.actor)
    await audit(db, p.actor, "zigbee_permit_join", bridge.name, home=str(body.home_id), seconds=body.seconds)
    await db.commit()
    until = datetime.now(UTC).timestamp() + body.seconds
    return JSONResponse(
        status_code=202,
        content={"command_id": str(cmd.id), "open_until": datetime.fromtimestamp(until, UTC).isoformat()},
    )
