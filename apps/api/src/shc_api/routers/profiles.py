"""Comfort profiles (verbeterlijst item 45).

A profile is a named comfort setting: target, comfort band, mode, energy priority and an
optional week schedule. Applying a profile to a zone replaces the zone's schedule and sets
its band and energy priority; editing a profile re-applies it to the zones that follow it.
A zone whose schedule is edited directly stops following its profile. Experiment variants
can refer to a profile, which the worker then switches automatically.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import views
from ..commanding import apply_profile, lock_outdoor_group, publish_zone, zones_using_profile
from ..deps import DB, Admin, Auth, get_home
from ..errors import api_error, not_found
from ..models import ClimateZone, ComfortProfile, Device, Experiment
from ..store import audit
from .zones import Block, Mode, validate_blocks

router = APIRouter(tags=["profiles"])


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    target_c: float = Field(ge=10, le=30)
    band_c: float = Field(default=0.5, ge=0.2, le=3)
    mode: Mode | None = None
    energy_priority: Literal["comfort", "balanced", "saving"] = "balanced"
    schedule: list[Block] = Field(default_factory=list, max_length=100)


class ApplyIn(BaseModel):
    profile_id: uuid.UUID


async def profile_view(db, pr: ComfortProfile) -> dict:
    zones = await zones_using_profile(db, pr.id)
    return {
        "id": pr.id,
        "home_id": pr.home_id,
        "name": pr.name,
        "target_c": pr.target_c,
        "band_c": pr.band_c,
        "mode": pr.mode,
        "energy_priority": pr.energy_priority,
        "schedule": pr.schedule or [],
        "zone_ids": [z.id for z in zones],
        "created_at": pr.created_at,
    }


async def _profile(db, profile_id: uuid.UUID) -> ComfortProfile:
    pr = await db.get(ComfortProfile, profile_id)
    if pr is None:
        raise not_found("Profiel")
    return pr


def _schedule_json(blocks: list[Block]) -> list[dict]:
    return [
        {"days": sorted(set(b.days)), "start": b.start, "target_c": b.target_c, "mode": b.mode, "label": b.label}
        for b in blocks
    ]


async def _commit_unique(db) -> None:
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise api_error(409, "conflict", "Er bestaat al een profiel met deze naam.") from e


@router.get("/homes/{home_id}/profiles")
async def list_profiles(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    await get_home(db, home_id)
    rows = (
        await db.execute(select(ComfortProfile).where(ComfortProfile.home_id == home_id).order_by(ComfortProfile.name))
    ).scalars()
    return [await profile_view(db, pr) for pr in rows]


@router.post("/homes/{home_id}/profiles")
async def create_profile(home_id: uuid.UUID, body: ProfileIn, db: DB, p: Admin) -> dict:
    await get_home(db, home_id)
    validate_blocks(body.schedule, None)
    pr = ComfortProfile(
        home_id=home_id,
        name=body.name.strip(),
        target_c=body.target_c,
        band_c=body.band_c,
        mode=body.mode,
        energy_priority=body.energy_priority,
        schedule=_schedule_json(body.schedule),
    )
    db.add(pr)
    await audit(db, p.actor, "profile_created", body.name)
    await _commit_unique(db)
    return await profile_view(db, pr)


@router.get("/profiles/{profile_id}")
async def get_profile(profile_id: uuid.UUID, db: DB, p: Auth) -> dict:
    return await profile_view(db, await _profile(db, profile_id))


@router.put("/profiles/{profile_id}")
async def update_profile(profile_id: uuid.UUID, body: ProfileIn, db: DB, p: Admin) -> dict:
    """Replace a profile. Zones that follow it get the new schedule, band and priority right away."""
    pr = await _profile(db, profile_id)
    validate_blocks(body.schedule, None)
    pr.name = body.name.strip()
    pr.target_c, pr.band_c, pr.mode = body.target_c, body.band_c, body.mode
    pr.energy_priority = body.energy_priority
    pr.schedule = _schedule_json(body.schedule)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise api_error(409, "conflict", "Er bestaat al een profiel met deze naam.") from e
    home = await get_home(db, pr.home_id)
    for z in await zones_using_profile(db, pr.id):
        await lock_outdoor_group(db, z)
        await db.refresh(z)
        await apply_profile(db, z, pr, p.actor, log_name=p.user.username)
        await db.flush()
        await publish_zone(db, z, home)
    await audit(db, p.actor, "profile_updated", str(pr.id), name=pr.name)
    await _commit_unique(db)
    return await profile_view(db, pr)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: uuid.UUID, db: DB, p: Admin) -> Response:
    """Zones keep their current schedule; running experiments that use the profile block deletion."""
    pr = await _profile(db, profile_id)
    running = (
        await db.execute(
            select(Experiment).where(
                Experiment.home_id == pr.home_id, Experiment.status.in_(("defined", "running", "paused"))
            )
        )
    ).scalars()
    for e in running:
        if any(v.get("profile_id") == str(pr.id) for v in e.variants or []):
            raise api_error(409, "conflict", f"Experiment '{e.name}' gebruikt dit profiel nog.")
    for z in await zones_using_profile(db, pr.id):
        z.profile_id = None
    await db.delete(pr)
    await audit(db, p.actor, "profile_deleted", str(profile_id), name=pr.name)
    await db.commit()
    return Response(status_code=204)


@router.post("/zones/{zone_id}/profile")
async def apply_to_zone(zone_id: uuid.UUID, body: ApplyIn, db: DB, p: Admin) -> dict:
    """Apply a profile: replaces the zone's schedule; a running manual override stays as it is."""
    z = await db.get(ClimateZone, zone_id)
    if z is None:
        raise not_found("Zone")
    pr = await _profile(db, body.profile_id)
    if pr.home_id != z.home_id:
        raise api_error(422, "validation_error", "Dit profiel hoort bij een andere woning.")
    if z.unit_device_id:
        unit = await db.get(Device, z.unit_device_id)
        blocks = [Block(**b) for b in pr.schedule or []] or [
            Block(days=list(range(7)), start="00:00", target_c=pr.target_c, mode=pr.mode)
        ]
        validate_blocks(blocks, unit)
    home = await get_home(db, z.home_id)
    await lock_outdoor_group(db, z)
    await db.refresh(z)
    await apply_profile(db, z, pr, p.actor, log_name=p.user.username)
    await db.flush()
    await publish_zone(db, z, home)
    await db.commit()
    return await views.zone_view(db, z, home)
