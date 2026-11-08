from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import views
from ..deps import DB, Admin, Auth, get_home
from ..events import dumps
from ..models import Device, DeviceBinding, LogicalRole, SensorReading, Tariff
from ..store import audit

router = APIRouter(tags=["energy"])


@router.get("/homes/{home_id}/energy/summary")
async def summary(
    home_id: uuid.UUID,
    db: DB,
    p: Auth,
    period: Literal["day", "week", "month", "year"] = "day",
    date_: date | None = Query(None, alias="date"),
) -> dict:
    home = await get_home(db, home_id)
    day = date_ or datetime.now(ZoneInfo(home.timezone)).date()
    return await views.energy_summary(db, home, period, day)


@router.get("/homes/{home_id}/energy/live")
async def live(home_id: uuid.UUID, db: DB, p: Auth) -> dict | None:
    await get_home(db, home_id)
    return await views.live_power(db, home_id)


def tariff_view(t: Tariff) -> dict:
    return {
        "id": t.id,
        "valid_from": t.valid_from,
        "import_per_kwh": float(t.import_per_kwh),
        "export_per_kwh": float(t.export_per_kwh),
        "fixed_per_day": float(t.fixed_per_day),
        "source": t.source,
    }


@router.get("/homes/{home_id}/tariffs")
async def tariffs(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    rows = (
        await db.execute(select(Tariff).where(Tariff.home_id == home_id).order_by(Tariff.valid_from.desc()))
    ).scalars()
    return [tariff_view(t) for t in rows]


class TariffIn(BaseModel):
    valid_from: date
    import_per_kwh: float = Field(ge=0, le=5)
    export_per_kwh: float = Field(default=0, ge=-1, le=5)
    fixed_per_day: float = Field(default=0, ge=0, le=50)


@router.post("/homes/{home_id}/tariffs")
async def add_tariff(home_id: uuid.UUID, body: TariffIn, db: DB, p: Admin) -> dict:
    await get_home(db, home_id)
    t = Tariff(home_id=home_id, source="handmatig", **body.model_dump())
    db.add(t)
    await audit(db, p.actor, "tariff_added", str(body.valid_from))
    await db.commit()
    return tariff_view(t)


@router.get("/homes/{home_id}/energy/export")
async def export(
    home_id: uuid.UUID,
    db: DB,
    p: Auth,
    format: Literal["csv", "json"] = "csv",
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
):
    """Raw counter/power readings of all energy-role devices, with quality and provenance."""
    home = await get_home(db, home_id)
    tz = ZoneInfo(home.timezone)
    end_d = to or datetime.now(tz).date()
    start_d = from_ or end_d - timedelta(days=30)
    start = datetime.combine(start_d, time(0), tz).astimezone(UTC)
    end = datetime.combine(end_d + timedelta(days=1), time(0), tz).astimezone(UTC)
    q = (
        select(SensorReading, Device.name, LogicalRole.key)
        .join(Device, Device.id == SensorReading.device_id)
        .join(DeviceBinding, DeviceBinding.device_id == Device.id)
        .join(LogicalRole, LogicalRole.id == DeviceBinding.role_id)
        .where(
            LogicalRole.home_id == home_id,
            LogicalRole.key.in_(["grid", "airco", "boiler"]),
            SensorReading.observed_at >= start,
            SensorReading.observed_at < end,
            SensorReading.metric.in_(["energy_import_kwh", "energy_export_kwh", "power_w", "gas_m3"]),
        )
        .order_by(SensorReading.observed_at)
    )
    rows = (await db.execute(q)).all()
    fname = f"energie_{home.name}_{start_d}_{end_d}".replace(" ", "_")
    if format == "json":
        data = [
            {
                "observed_at": r.SensorReading.observed_at,
                "role": r.key,
                "device": r.name,
                "metric": r.SensorReading.metric,
                "value": r.SensorReading.value,
                "unit": r.SensorReading.unit,
                "quality": r.SensorReading.quality,
            }
            for r in rows
        ]
        body = dumps({"schema": "shc.energy.v1", "home": home.name, "timezone": home.timezone, "rows": data})
        return StreamingResponse(
            iter([body]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{fname}.json"'},
        )
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["tijd_utc", "tijd_lokaal", "rol", "apparaat", "meting", "waarde", "eenheid", "kwaliteit"])
    for r in rows:
        s = r.SensorReading
        w.writerow(
            [
                s.observed_at.isoformat(),
                s.observed_at.astimezone(tz).isoformat(),
                r.key,
                r.name,
                s.metric,
                str(s.value).replace(".", ","),
                s.unit,
                s.quality,
            ]
        )
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'},
    )


_ = json
