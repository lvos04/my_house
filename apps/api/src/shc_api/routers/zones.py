from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select

from .. import views
from ..commanding import (
    CommandRejected,
    lock_outdoor_group,
    publish_zone,
    request_zone_command,
    resume_zone,
)
from ..deps import DB, Admin, Auth, Control, get_home
from ..errors import api_error, not_found
from ..models import ClimateZone, ComfortProfile, Device, Home, ScheduleBlock, SensorReading, ZoneLog
from ..store import audit, device_for_role
from ..views.common import CLIMATE_ROLES, caps_of

router = APIRouter(tags=["climate"])

Mode = Literal["off", "heat", "cool", "auto", "dry", "fan"]
DAY_NAMES = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]


async def _zone(db: DB, zone_id: uuid.UUID) -> tuple[ClimateZone, Home]:
    z = await db.get(ClimateZone, zone_id)
    if not z:
        raise not_found("Zone")
    return z, await get_home(db, z.home_id)


@router.get("/homes/{home_id}/zones")
async def list_zones(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    home = await get_home(db, home_id)
    zs = (
        await db.execute(select(ClimateZone).where(ClimateZone.home_id == home_id).order_by(ClimateZone.name.desc()))
    ).scalars()
    return [await views.zone_view(db, z, home) for z in zs]


@router.get("/zones/{zone_id}")
async def get_zone(zone_id: uuid.UUID, db: DB, p: Auth) -> dict:
    z, h = await _zone(db, zone_id)
    return await views.zone_view(db, z, h)


class ZoneCommand(BaseModel):
    mode: Mode | None = None
    target_c: float | None = Field(default=None, ge=5, le=35)
    fan: Literal["auto", "low", "medium", "high"] | None = None
    override_minutes: int | None = Field(default=None, ge=5, le=24 * 60)


@router.post("/zones/{zone_id}/command", status_code=202)
async def command(zone_id: uuid.UUID, body: ZoneCommand, db: DB, p: Control):
    z, home = await _zone(db, zone_id)
    try:
        cmd = await request_zone_command(
            db,
            z,
            home,
            mode=body.mode,
            target_c=body.target_c,
            fan=body.fan,
            override_minutes=body.override_minutes,
            source="user",
            actor=p.actor,
            log_name=p.user.username,
        )
    except CommandRejected as e:
        raise api_error(e.status, e.code, e.message) from e
    await db.commit()
    return JSONResponse({"command_id": str(cmd.id), "status": "queued"}, status_code=202)


@router.post("/zones/{zone_id}/resume")
async def resume(zone_id: uuid.UUID, db: DB, p: Control) -> dict:
    """Back to schedule, also from a deliberate "off". Viewers get 403."""
    z, home = await _zone(db, zone_id)
    await resume_zone(db, z, home, p.actor, log_name=p.user.username)
    view = await views.zone_view(db, z, home)
    await db.commit()
    return view


@router.get("/zones/{zone_id}/history")
async def history(zone_id: uuid.UUID, db: DB, p: Auth, hours: int = Query(24, ge=1, le=24 * 31)) -> dict:
    z, _ = await _zone(db, zone_id)
    since = datetime.now(UTC) - timedelta(hours=hours)
    sensor = z.sensor_device_id or z.unit_device_id
    out: dict[str, list] = {"temperature": [], "target": []}
    for key, dev, metric in (
        ("temperature", sensor, "temperature"),
        ("target", z.unit_device_id, "target_c"),
    ):
        if dev is None:
            continue
        rows = (
            await db.execute(
                select(SensorReading.observed_at, SensorReading.value)
                .where(
                    SensorReading.device_id == dev,
                    SensorReading.metric == metric,
                    SensorReading.observed_at >= since,
                )
                .order_by(SensorReading.observed_at)
            )
        ).all()
        out[key] = [[r.observed_at, r.value] for r in rows]
    return out


@router.get("/zones/{zone_id}/log")
async def zone_log(zone_id: uuid.UUID, db: DB, p: Auth, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    rows = (
        await db.execute(select(ZoneLog).where(ZoneLog.zone_id == zone_id).order_by(ZoneLog.at.desc()).limit(limit))
    ).scalars()
    return [{"at": r.at, "kind": r.kind, "message": r.message} for r in rows]


# --- schedule ---------------------------------------------------------------------


class Block(BaseModel):
    """One schedule block. Without ``mode`` the block keeps the mode the unit runs in and never
    starts a unit that is off; ``mode: "off"`` switches the unit off at ``start``."""

    days: list[int] = Field(min_length=1, max_length=7)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    target_c: float = Field(ge=10, le=30)
    mode: Mode | None = None
    label: str = Field(default="", max_length=50)


class Schedule(BaseModel):
    blocks: list[Block] = Field(max_length=100)


def validate_blocks(blocks: list[Block], unit: Device | None) -> None:
    """Raise a 422 with a plain Dutch message for the first problem found."""
    seen: dict[tuple[int, str], int] = {}
    caps = caps_of(unit) if unit is not None else None
    for i, b in enumerate(blocks, start=1):
        if any(d < 0 or d > 6 for d in b.days):
            raise api_error(422, "validation_error", "Dagen moeten 0 (maandag) t/m 6 (zondag) zijn.")
        if len(set(b.days)) != len(b.days):
            raise api_error(422, "validation_error", f"Blok {i} noemt dezelfde dag twee keer.")
        for d in b.days:
            if (d, b.start) in seen:
                raise api_error(
                    422,
                    "validation_error",
                    f"Op {DAY_NAMES[d]} beginnen twee blokken om {b.start} (blok {seen[(d, b.start)]} en {i}).",
                )
            seen[(d, b.start)] = i
        if caps is not None:
            if b.mode not in (None, "off") and b.mode not in caps.modes:
                raise api_error(
                    422, "validation_error", f"Blok {i}: modus '{b.mode}' wordt door de airco niet ondersteund."
                )
            if not caps.min_c <= b.target_c <= caps.max_c:
                raise api_error(
                    422,
                    "validation_error",
                    f"Blok {i}: {b.target_c:g} °C ligt buiten het bereik van de airco "
                    f"({caps.min_c:g}–{caps.max_c:g} °C).",
                )


def block_view(b: ScheduleBlock) -> dict:
    return {"days": b.days, "start": b.start, "target_c": b.target_c, "mode": b.mode, "label": b.label}


@router.get("/zones/{zone_id}/schedule")
async def get_schedule(zone_id: uuid.UUID, db: DB, p: Auth) -> dict:
    z, _ = await _zone(db, zone_id)
    rows = (await db.execute(select(ScheduleBlock).where(ScheduleBlock.zone_id == zone_id))).scalars()
    profile = await db.get(ComfortProfile, z.profile_id) if z.profile_id else None
    return {
        "blocks": [block_view(b) for b in sorted(rows, key=lambda b: (b.start, min(b.days or [0])))],
        "profile_id": z.profile_id,
        "profile": profile.name if profile else None,
    }


@router.put("/zones/{zone_id}/schedule")
async def put_schedule(zone_id: uuid.UUID, body: Schedule, db: DB, p: Admin) -> dict:
    z, home = await _zone(db, zone_id)
    unit = await db.get(Device, z.unit_device_id) if z.unit_device_id else None
    validate_blocks(body.blocks, unit)
    await lock_outdoor_group(db, z)
    await db.refresh(z)
    await db.execute(delete(ScheduleBlock).where(ScheduleBlock.zone_id == zone_id))
    for b in body.blocks:
        db.add(
            ScheduleBlock(
                zone_id=zone_id,
                days=sorted(set(b.days)),
                start=b.start,
                target_c=b.target_c,
                mode=b.mode,
                label=b.label,
            )
        )
    if z.profile_id is not None:  # an own schedule: the zone no longer follows its profile
        z.profile_id, z.profile = None, None
    db.add(ZoneLog(zone_id=z.id, kind="config", message=f"{p.user.username}: schema aangepast"))
    await audit(db, p.actor, "schedule_updated", str(zone_id), blocks=len(body.blocks))
    await db.flush()
    await publish_zone(db, z, home)
    await db.commit()
    return await get_schedule(zone_id, db, p)


# --- policy -----------------------------------------------------------------------


class PolicyIn(BaseModel):
    """Zone regulation limits. Only the fields sent are changed."""

    band_c: float | None = Field(default=None, ge=0.2, le=3)
    min_interval_s: int | None = Field(default=None, ge=300, le=7200)
    max_step_c: float | None = Field(default=None, ge=0.5, le=3)
    max_starts_per_hour: int | None = Field(default=None, ge=1, le=6)
    stale_after_s: int | None = Field(default=None, ge=300, le=7200)
    plausible_min_c: float | None = Field(default=None, ge=-30, le=30)
    plausible_max_c: float | None = Field(default=None, ge=10, le=60)
    max_offset_c: float | None = Field(default=None, ge=0, le=3)
    override_default_minutes: int | None = Field(default=None, ge=15, le=24 * 60)
    energy_priority: Literal["comfort", "balanced", "saving"] | None = None
    watchdog_minutes: int | None = Field(default=None, ge=0, le=720)
    watchdog_min_progress_c: float | None = Field(default=None, ge=0.1, le=3)

    @model_validator(mode="after")
    def _check(self):
        if self.watchdog_minutes is not None and 0 < self.watchdog_minutes < 15:
            raise ValueError("Waakhond: kies 0 (uit) of minstens 15 minuten")
        return self


@router.put("/zones/{zone_id}/policy")
async def put_policy(zone_id: uuid.UUID, body: PolicyIn, db: DB, p: Admin) -> dict:
    z, h = await _zone(db, zone_id)
    changes = body.model_dump(exclude_none=True)
    merged = {**(z.policy or {}), **changes}
    lo = merged.get("plausible_min_c", 5.0)
    hi = merged.get("plausible_max_c", 40.0)
    if lo >= hi:
        raise api_error(422, "validation_error", "De ondergrens voor sensorwaarden moet lager zijn dan de bovengrens.")
    z.policy = merged
    db.add(ZoneLog(zone_id=z.id, kind="config", message=f"{p.user.username}: regelgrenzen aangepast"))
    await audit(db, p.actor, "zone_policy_updated", str(zone_id), **changes)
    await publish_zone(db, z, h)
    await db.commit()
    return await views.zone_view(db, z, h)


# --- zone configuration -----------------------------------------------------------


class ZoneConfig(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    unit_device_id: uuid.UUID | None = None
    sensor_device_id: uuid.UUID | None = None
    outdoor_group: str | None = Field(default=None, max_length=64)


@router.patch("/zones/{zone_id}")
async def patch_zone(zone_id: uuid.UUID, body: ZoneConfig, db: DB, p: Admin) -> dict:
    z, h = await _zone(db, zone_id)
    for f in ("unit_device_id", "sensor_device_id"):
        if f in body.model_fields_set and getattr(body, f) is not None:
            d = await db.get(Device, getattr(body, f))
            if d is None or d.home_id != z.home_id:
                raise api_error(422, "validation_error", "Dit apparaat hoort niet bij deze woning.")
    await lock_outdoor_group(db, z)
    await db.refresh(z)
    unit_changed = "unit_device_id" in body.model_fields_set and body.unit_device_id != z.unit_device_id
    for f in body.model_fields_set:
        setattr(z, f, getattr(body, f))
    if unit_changed:  # another unit: start command tracking from scratch
        z.last_commanded, z.mismatch_count, z.correction_c = {}, 0, 0.0
        z.pending_command_id, z.pending_since = None, None
        if z.control in ("manual", "off"):
            z.control, z.override_until, z.override_source = "schedule", None, None
    db.add(ZoneLog(zone_id=z.id, kind="config", message=f"{p.user.username}: zone-koppeling aangepast"))
    await audit(db, p.actor, "zone_updated", str(zone_id), fields=sorted(body.model_fields_set))
    await db.flush()
    await publish_zone(db, z, h)
    await db.commit()
    return await views.zone_view(db, z, h)


@router.get("/homes/{home_id}/climate/roles")
async def climate_roles(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    """Climate roles a device can be bound to (POST /devices/{id}/bind), e.g. the outdoor sensor."""
    await get_home(db, home_id)
    out = []
    for key, label in CLIMATE_ROLES:
        d = await device_for_role(db, home_id, key)
        out.append(
            {"role": key, "label": label, "device_id": d.id if d else None, "device_name": d.name if d else None}
        )
    return out
