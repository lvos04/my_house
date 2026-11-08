from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from .. import views
from ..deps import DB, Admin, Auth, get_home
from ..errors import not_found
from ..models import Alert, ClimateZone, Home, LogicalRole, Room
from ..store import audit

router = APIRouter(tags=["homes"])

DEFAULT_ROLES = [
    ("grid", "Huis totaal (P1)", False),
    ("airco", "Airco totaal (1-fase kWh-meter)", False),
    ("boiler", "Boiler (alleen meten)", True),
    ("living_room_climate", "Klimaat woonkamer", False),
    ("bedroom_climate", "Klimaat slaapkamer", False),
]


class HomeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    timezone: str = "Europe/Amsterdam"
    currency: str = Field(default="EUR", min_length=3, max_length=3)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        if v not in available_timezones():
            raise ValueError("Onbekende tijdzone")
        return v


@router.get("/homes")
async def homes(db: DB, p: Auth) -> list[dict]:
    rows = (await db.execute(select(Home).order_by(Home.active_from))).scalars()
    return [views.home_view(h) for h in rows]


@router.post("/homes")
async def create_home(body: HomeIn, db: DB, p: Admin) -> dict:
    h = Home(name=body.name, timezone=body.timezone, currency=body.currency)
    db.add(h)
    await db.flush()
    for key, label, protected in DEFAULT_ROLES:
        db.add(LogicalRole(home_id=h.id, key=key, label=label, protected=protected))
    lr = Room(home_id=h.id, name="Woonkamer", function="living")
    br = Room(home_id=h.id, name="Slaapkamer", function="bedroom")
    db.add_all([lr, br])
    await db.flush()
    db.add_all(
        [
            ClimateZone(home_id=h.id, name="Woonkamer", room_id=lr.id, outdoor_group="multisplit-1"),
            ClimateZone(home_id=h.id, name="Slaapkamer", room_id=br.id, outdoor_group="multisplit-1"),
        ]
    )
    await audit(db, p.actor, "home_created", body.name)
    await db.commit()
    return views.home_view(h)


@router.get("/homes/{home_id}/overview")
async def overview(home_id: uuid.UUID, db: DB, p: Auth) -> dict:
    home = await get_home(db, home_id)
    zones = (
        (await db.execute(select(ClimateZone).where(ClimateZone.home_id == home_id).order_by(ClimateZone.name.desc())))
        .scalars()
        .all()
    )
    tz = ZoneInfo(home.timezone)
    today = datetime.now(tz).date()
    return {
        "home": views.home_view(home),
        "status": await views.status_checks(db, home_id),
        "zones": [await views.zone_view(db, z, home) for z in zones],
        "lights": (await views.lights_view(db, home_id))["groups"],
        "energy_today": await views.energy_summary(db, home, "day", today),
        "power_now": await views.live_power(db, home_id),
        "alerts": await views.open_alerts(db, home_id),
    }


@router.get("/homes/{home_id}/alerts")
async def alerts(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    await get_home(db, home_id)
    return await views.open_alerts(db, home_id, 100)


@router.post("/alerts/{alert_id}/ack")
async def ack(alert_id: uuid.UUID, db: DB, p: Auth) -> dict:
    a = await db.get(Alert, alert_id)
    if not a:
        raise not_found("Melding")
    a.acknowledged_at = datetime.now(UTC)
    await db.commit()
    from ..store import alert_view

    return alert_view(a)


@router.get("/homes/{home_id}/rooms")
async def rooms(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    rows = (await db.execute(select(Room).where(Room.home_id == home_id))).scalars()
    return [{"id": r.id, "name": r.name, "function": r.function} for r in rows]


_ = date  # imported for type clarity in views
