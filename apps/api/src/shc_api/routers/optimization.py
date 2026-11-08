"""Optimisation: adaptive model per zone (opt-in, versioned, rollback) and period experiments."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, update

from .. import adaptive as ad
from .. import energy as en
from .. import views
from ..deps import DB, Admin, Auth, get_home
from ..errors import api_error, not_found
from ..models import AdaptiveModelVersion, ClimateZone, CommandRecord, Experiment, SensorReading, ZoneLog
from ..store import audit, device_for_role

router = APIRouter(tags=["optimization"])
BASELINE_DAYS = 14


async def _series(db, device_id, metric, since):
    if device_id is None:
        return []
    rows = (
        await db.execute(
            select(SensorReading.observed_at, SensorReading.value)
            .where(
                SensorReading.device_id == device_id,
                SensorReading.metric == metric,
                SensorReading.observed_at >= since,
                SensorReading.value.is_not(None),
            )
            .order_by(SensorReading.observed_at)
        )
    ).all()
    return [(r.observed_at, r.value) for r in rows]


async def zone_samples(db, z: ClimateZone, days: int = BASELINE_DAYS) -> list[ad.Sample]:
    since = datetime.now(UTC) - timedelta(days=days)
    temps = await _series(db, z.sensor_device_id or z.unit_device_id, "temperature", since)
    sps = await _series(db, z.unit_device_id, "target_c", since)
    # Running = a setpoint reading exists and the unit was not commanded off afterwards.
    offs = (
        await db.execute(
            select(CommandRecord.created_at, CommandRecord.params)
            .where(
                CommandRecord.zone_id == z.id,
                CommandRecord.created_at >= since,
                CommandRecord.status.in_(["confirmed", "sent"]),
            )
            .order_by(CommandRecord.created_at)
        )
    ).all()
    modes = [(r.created_at, r.params.get("mode", "heat")) for r in offs if "mode" in r.params]
    if not modes and sps:
        modes = [(sps[0][0], "heat")]
    return ad.resample(temps, sps, modes)


def model_view(m: AdaptiveModelVersion) -> dict:
    return {"id": m.id, "version": m.version, "params": m.params, "created_at": m.created_at, "active": m.active}


@router.get("/zones/{zone_id}/adaptive")
async def adaptive_status(zone_id: uuid.UUID, db: DB, p: Auth) -> dict:
    z = await db.get(ClimateZone, zone_id)
    if not z:
        raise not_found("Zone")
    samples = await zone_samples(db, z)
    running = sum(1 for s in samples if s.running)
    models = (
        (
            await db.execute(
                select(AdaptiveModelVersion)
                .where(AdaptiveModelVersion.zone_id == zone_id)
                .order_by(AdaptiveModelVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    first = samples[0].at if samples else None
    return {
        "enabled": z.adaptive_enabled,
        "baseline": {
            "samples": len(samples),
            "running_samples": running,
            "required": ad.MIN_SAMPLES,
            "since": first,
            "ready": running >= ad.MIN_SAMPLES,
        },
        "max_offset_c": ad.MAX_ADAPTIVE_OFFSET,
        "models": [model_view(m) for m in models],
    }


@router.post("/zones/{zone_id}/adaptive/fit")
async def adaptive_fit(zone_id: uuid.UUID, db: DB, p: Admin) -> dict:
    z = await db.get(ClimateZone, zone_id)
    if not z:
        raise not_found("Zone")
    params = ad.fit(await zone_samples(db, z))
    if params is None:
        raise api_error(
            409,
            "insufficient_data",
            "Nog te weinig meetgegevens. De baseline verzamelt eerst passief "
            f"minstens {ad.MIN_SAMPLES} kwartieren waarin de airco draait.",
        )
    last = (
        await db.execute(select(func.max(AdaptiveModelVersion.version)).where(AdaptiveModelVersion.zone_id == zone_id))
    ).scalar_one() or 0
    m = AdaptiveModelVersion(zone_id=zone_id, version=last + 1, params=params, active=False)
    db.add(m)
    db.add(ZoneLog(zone_id=zone_id, kind="adaptive", message=f"Model v{last + 1} berekend (R² {params['r2']})"))
    await audit(db, p.actor, "adaptive_fit", str(zone_id), version=last + 1)
    await db.commit()
    return model_view(m)


class Activate(BaseModel):
    model_id: uuid.UUID


@router.post("/zones/{zone_id}/adaptive/activate")
async def adaptive_activate(zone_id: uuid.UUID, body: Activate, db: DB, p: Admin) -> dict:
    """Opt in with a specific version. Activating an older version is the rollback."""
    z = await db.get(ClimateZone, zone_id)
    m = await db.get(AdaptiveModelVersion, body.model_id)
    if not z or not m or m.zone_id != zone_id:
        raise not_found("Model")
    if not m.params.get("usable"):
        raise api_error(409, "policy_denied", "Dit model is niet betrouwbaar genoeg (te lage R² of reactie).")
    await db.execute(update(AdaptiveModelVersion).where(AdaptiveModelVersion.zone_id == zone_id).values(active=False))
    m.active = True
    z.adaptive_enabled = True
    db.add(ZoneLog(zone_id=zone_id, kind="adaptive", message=f"{p.user.username}: model v{m.version} actief"))
    await audit(db, p.actor, "adaptive_activate", str(zone_id), version=m.version)
    await db.commit()
    return await adaptive_status(zone_id, db, p)


@router.post("/zones/{zone_id}/adaptive/disable")
async def adaptive_disable(zone_id: uuid.UUID, db: DB, p: Admin) -> dict:
    z = await db.get(ClimateZone, zone_id)
    if not z:
        raise not_found("Zone")
    z.adaptive_enabled = False
    await db.execute(update(AdaptiveModelVersion).where(AdaptiveModelVersion.zone_id == zone_id).values(active=False))
    db.add(ZoneLog(zone_id=zone_id, kind="adaptive", message=f"{p.user.username}: adaptief uitgezet"))
    await db.commit()
    return await adaptive_status(zone_id, db, p)


# --- experiments --------------------------------------------------------------------


class Variant(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("Einde moet na begin liggen")
        return self


class ExperimentIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    zone_id: uuid.UUID
    variants: list[Variant] = Field(min_length=2, max_length=4)


@router.get("/homes/{home_id}/experiments")
async def experiments(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    rows = (
        await db.execute(select(Experiment).where(Experiment.home_id == home_id).order_by(Experiment.created_at.desc()))
    ).scalars()
    return [
        {"id": e.id, "name": e.name, "variants": e.variants, "status": e.status, "created_at": e.created_at}
        for e in rows
    ]


@router.post("/homes/{home_id}/experiments")
async def create_experiment(home_id: uuid.UUID, body: ExperimentIn, db: DB, p: Admin) -> dict:
    await get_home(db, home_id)
    e = Experiment(
        home_id=home_id,
        name=body.name,
        status="defined",
        variants=[
            {"label": v.label, "start": v.start.isoformat(), "end": v.end.isoformat(), "zone_id": str(body.zone_id)}
            for v in body.variants
        ],
    )
    db.add(e)
    await db.commit()
    return {"id": e.id, "name": e.name, "variants": e.variants, "status": e.status}


@router.get("/experiments/{exp_id}")
async def experiment_result(exp_id: uuid.UUID, db: DB, p: Auth) -> dict:
    e = await db.get(Experiment, exp_id)
    if not e:
        raise not_found("Experiment")
    home = await get_home(db, e.home_id)
    stats = []
    outdoor = await device_for_role(db, home.id, "outdoor_temp")
    for v in e.variants:
        start, end = datetime.fromisoformat(v["start"]), datetime.fromisoformat(v["end"])
        end = min(end, datetime.now(UTC))
        days = max(0.0, (end - start).total_seconds() / 86400)
        z = await db.get(ClimateZone, uuid.UUID(v["zone_id"]))
        kwh = None
        if days > 0:
            b = [en.Bucket(start, end)]
            s = await views.energy_series(db, home.id, "airco", "energy_import_kwh", b)
            kwh = round(s[0].kwh, 3) if s else None
        temps = await _series(db, z.sensor_device_id or z.unit_device_id, "temperature", start) if z else []
        sps = await _series(db, z.unit_device_id, "target_c", start) if z else []
        temps = [t for t in temps if t[0] <= end]
        dev = None
        if temps and sps:
            diffs = []
            j = 0
            for at, val in temps:
                while j + 1 < len(sps) and sps[j + 1][0] <= at:
                    j += 1
                if sps[j][0] <= at:
                    diffs.append(abs(val - sps[j][1]))
            dev = round(sum(diffs) / len(diffs), 2) if diffs else None
        starts = (
            await db.execute(
                select(func.count())
                .select_from(CommandRecord)
                .where(
                    CommandRecord.zone_id == (z.id if z else None),
                    CommandRecord.created_at >= start,
                    CommandRecord.created_at <= end,
                    CommandRecord.params["mode"].astext.in_(["heat", "cool", "auto", "dry"]),
                )
            )
        ).scalar_one()
        out = None
        if outdoor:
            ot = [x[1] for x in await _series(db, outdoor.id, "temperature", start) if x[0] <= end]
            out = round(sum(ot) / len(ot), 1) if ot else None
        stats.append(ad.PeriodStats(v["label"], days, kwh, dev, starts, out))
    return {"id": e.id, "name": e.name, "variants": e.variants, **ad.compare(stats)}
