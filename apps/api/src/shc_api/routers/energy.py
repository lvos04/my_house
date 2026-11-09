from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select

from .. import energy as en
from .. import views
from ..db import sessionmaker
from ..deps import DB, Admin, Auth, get_home
from ..errors import api_error, not_found
from ..events import dumps
from ..models import Home, Tariff
from ..store import audit, get_setting, set_setting
from ..views.energy import export_rows, rate_overrides, tariff_extras

router = APIRouter(tags=["energy"])

RAW_EXPORT_MAX_DAYS = 31
EXPORT_FLUSH_BYTES = 64 * 1024


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


# --- tariffs -------------------------------------------------------------------------------


def tariff_view(t: Tariff, extras: dict | None = None) -> dict:
    def f(v):
        return None if v is None else float(v)

    return {
        "id": t.id,
        "valid_from": t.valid_from,
        "import_per_kwh": float(t.import_per_kwh),
        "export_per_kwh": float(t.export_per_kwh or 0),
        "fixed_per_day": float(t.fixed_per_day or 0),
        "import_t1_per_kwh": f(t.import_t1_per_kwh),
        "import_t2_per_kwh": f(t.import_t2_per_kwh),
        "gas_per_m3": f(((extras or {}).get(str(t.id)) or {}).get("gas_per_m3")),
        "source": t.source,
    }


@router.get("/homes/{home_id}/tariffs")
async def tariffs(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    await get_home(db, home_id)
    extras = await tariff_extras(db)
    rows = (
        await db.execute(select(Tariff).where(Tariff.home_id == home_id).order_by(Tariff.valid_from.desc()))
    ).scalars()
    return [tariff_view(t, extras) for t in rows]


def _range(v: float | None, lo: float, hi: float, what: str) -> float | None:
    if v is None:
        return None
    if not (lo <= v <= hi):
        raise ValueError(f"{what} moet tussen {lo:g} en {hi:g} liggen")
    return v


class TariffIn(BaseModel):
    """Prices per kWh / m³ in the home's currency. Optional fields may be left empty."""

    valid_from: date
    import_per_kwh: float
    export_per_kwh: float | None = 0
    fixed_per_day: float | None = 0  # empty = no fixed costs (verbeterlijst 7)
    import_t1_per_kwh: float | None = None
    import_t2_per_kwh: float | None = None
    gas_per_m3: float | None = None

    @field_validator(
        "export_per_kwh", "fixed_per_day", "import_t1_per_kwh", "import_t2_per_kwh", "gas_per_m3", mode="before"
    )
    @classmethod
    def _empty_is_none(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, str):
            try:
                return float(v.replace(",", "."))
            except ValueError as exc:
                raise ValueError("Vul een getal in") from exc
        return v

    @model_validator(mode="after")
    def _check(self) -> TariffIn:
        _range(self.import_per_kwh, 0, 5, "Stroomprijs")
        self.export_per_kwh = _range(self.export_per_kwh, -1, 5, "Terugleververgoeding") or 0.0
        self.fixed_per_day = _range(self.fixed_per_day, 0, 50, "Vaste kosten per dag") or 0.0
        _range(self.import_t1_per_kwh, 0, 5, "Prijs normaal tarief (T1)")
        _range(self.import_t2_per_kwh, 0, 5, "Prijs dal tarief (T2)")
        _range(self.gas_per_m3, 0, 20, "Gasprijs")
        if (self.import_t1_per_kwh is None) != (self.import_t2_per_kwh is None):
            raise ValueError("Vul zowel de prijs voor normaal (T1) als voor dal (T2) in, of geen van beide")
        return self


def _columns(body: TariffIn) -> dict:
    return body.model_dump(exclude={"gas_per_m3"})


async def _set_gas(db, tariff_id: uuid.UUID, gas: float | None) -> None:
    extras = dict(await tariff_extras(db))
    if gas is None:
        extras.pop(str(tariff_id), None)
    else:
        extras[str(tariff_id)] = {"gas_per_m3": gas}
    await set_setting(db, "tariff_extras", extras)


async def _conflict(db, home_id: uuid.UUID, valid_from: date, exclude: uuid.UUID | None = None) -> None:
    q = select(Tariff.id).where(Tariff.home_id == home_id, Tariff.valid_from == valid_from)
    if exclude:
        q = q.where(Tariff.id != exclude)
    if (await db.execute(q)).first():
        raise api_error(
            409,
            "tariff_exists",
            f"Er is al een tarief dat ingaat op {valid_from:%d-%m-%Y}. Pas dat tarief aan of kies een andere datum.",
        )


@router.post("/homes/{home_id}/tariffs")
async def add_tariff(home_id: uuid.UUID, body: TariffIn, db: DB, p: Admin) -> dict:
    await get_home(db, home_id)
    await _conflict(db, home_id, body.valid_from)
    t = Tariff(home_id=home_id, source="handmatig", **_columns(body))
    db.add(t)
    await db.flush()
    await _set_gas(db, t.id, body.gas_per_m3)
    await audit(db, p.actor, "tariff_added", str(body.valid_from), **_audit_detail(body))
    await db.commit()
    return tariff_view(t, await tariff_extras(db))


def _audit_detail(body: TariffIn) -> dict:
    return {k: (str(v) if isinstance(v, date) else v) for k, v in body.model_dump().items()}


@router.put("/tariffs/{tariff_id}")
async def update_tariff(tariff_id: uuid.UUID, body: TariffIn, db: DB, p: Admin) -> dict:
    t = await db.get(Tariff, tariff_id)
    if not t:
        raise not_found("Tarief")
    await _conflict(db, t.home_id, body.valid_from, exclude=t.id)
    before = tariff_view(t, await tariff_extras(db))
    for k, v in _columns(body).items():
        setattr(t, k, v)
    t.source = "handmatig"
    await _set_gas(db, t.id, body.gas_per_m3)
    await audit(
        db,
        p.actor,
        "tariff_updated",
        str(body.valid_from),
        before={k: (str(v) if isinstance(v, date | uuid.UUID) else v) for k, v in before.items()},
        after=_audit_detail(body),
    )
    await db.commit()
    return tariff_view(t, await tariff_extras(db))


@router.delete("/tariffs/{tariff_id}", status_code=204)
async def delete_tariff(tariff_id: uuid.UUID, db: DB, p: Admin) -> Response:
    t = await db.get(Tariff, tariff_id)
    if not t:
        raise not_found("Tarief")
    await audit(
        db, p.actor, "tariff_deleted", str(t.valid_from), import_per_kwh=float(t.import_per_kwh), home=str(t.home_id)
    )
    await _set_gas(db, t.id, None)
    await db.delete(t)
    await db.commit()
    return Response(status_code=204)


# --- plausibility limits ---------------------------------------------------------------------


class LimitsIn(BaseModel):
    """Maximum plausible power per meter role in kW (gas in m³ per hour)."""

    grid: float | None = None
    airco: float | None = None
    boiler: float | None = None
    gas: float | None = None

    @model_validator(mode="after")
    def _check(self) -> LimitsIn:
        for k in ("grid", "airco", "boiler", "gas"):
            _range(getattr(self, k), 0.1, 100, f"Maximum voor {k}")
        return self


async def _limits(db) -> dict:
    o = await rate_overrides(db)
    return {
        "grid": o.get("grid", en.ROLE_MAX_KW["grid"]),
        "airco": o.get("airco", en.ROLE_MAX_KW["airco"]),
        "boiler": o.get("boiler", en.ROLE_MAX_KW["boiler"]),
        "gas": o.get("gas", en.GAS_MAX_M3_PER_H),
    }


@router.get("/energy/limits")
async def get_limits(db: DB, p: Auth) -> dict:
    return await _limits(db)


@router.put("/energy/limits")
async def put_limits(body: LimitsIn, db: DB, p: Admin) -> dict:
    cur = await get_setting(db, "energy_max_kw", {}) or {}
    for k, v in body.model_dump().items():
        if k in body.model_fields_set:
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
    await set_setting(db, "energy_max_kw", cur)
    await audit(db, p.actor, "energy_limits_updated", None, **cur)
    await db.commit()
    return await _limits(db)


# --- export ----------------------------------------------------------------------------------

CSV_HEADER = ["tijd_utc", "tijd_lokaal", "rol", "apparaat", "meting", "waarde", "eenheid", "kwaliteit", "opmerking"]


def _num(v) -> str:
    if v is None:
        return ""
    return str(v).replace(".", ",")


@router.get("/homes/{home_id}/energy/export")
async def export(
    home_id: uuid.UUID,
    db: DB,
    p: Auth,
    format: Literal["csv", "json"] = "csv",
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    resolution: Literal["hour", "day", "raw"] = "hour",
):
    """Streamed export. Default: hourly totals per meter (from the aggregates); `day` for daily
    totals; `raw` for the stored readings, at most 31 days at a time. Rows only cover the period
    in which a meter was bound to its role. Missing values are empty cells."""
    home = await get_home(db, home_id)
    tz = ZoneInfo(home.timezone)
    end_d = to or datetime.now(tz).date()
    start_d = from_ or end_d - timedelta(days=30)
    if start_d > end_d:
        raise api_error(422, "validation_error", "De begindatum ligt na de einddatum.")
    if resolution == "raw" and (end_d - start_d).days + 1 > RAW_EXPORT_MAX_DAYS:
        raise api_error(
            422,
            "range_too_large",
            f"Ruwe metingen kun je per maximaal {RAW_EXPORT_MAX_DAYS} dagen exporteren. "
            "Kies een kortere periode, of exporteer uur- of dagtotalen.",
        )
    start = datetime.combine(start_d, time(0), tz).astimezone(UTC)
    end = datetime.combine(end_d + timedelta(days=1), time(0), tz).astimezone(UTC)
    home_name, home_tz = home.name, home.timezone
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", home_name).strip("_") or "woning"
    fname = f"energie_{safe}_{start_d}_{end_d}_{resolution}"

    async def json_body():
        yield (
            '{"schema":"shc.energy.v2",'
            f'"home":{dumps(home_name)},"timezone":{dumps(home_tz)},"resolution":{dumps(resolution)},'
            f'"from":{dumps(start)},"to":{dumps(end)},"rows":['
        )
        buf: list[str] = []
        size = 0
        first = True
        async with sessionmaker()() as s:
            h = await s.get(Home, home_id)
            async for row in export_rows(s, h, start, end, resolution):
                piece = ("" if first else ",") + dumps(row)
                first = False
                buf.append(piece)
                size += len(piece)
                if size >= EXPORT_FLUSH_BYTES:
                    yield "".join(buf)
                    buf, size = [], 0
        buf.append("]}")
        yield "".join(buf)

    async def csv_body():
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow(CSV_HEADER)
        async with sessionmaker()() as s:
            h = await s.get(Home, home_id)
            async for r in export_rows(s, h, start, end, resolution):
                at = r["at"]
                w.writerow(
                    [
                        at.astimezone(UTC).isoformat(),
                        at.astimezone(tz).isoformat(),
                        r["role"],
                        r["device"],
                        r["metric"],
                        _num(r["value"]),
                        r["unit"] or "",
                        r["quality"] or "",
                        r.get("notes") or "",
                    ]
                )
                if out.tell() >= EXPORT_FLUSH_BYTES:
                    yield out.getvalue()
                    out.seek(0)
                    out.truncate()
        yield out.getvalue()

    if format == "json":
        return StreamingResponse(
            json_body(),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{fname}.json"'},
        )
    return StreamingResponse(
        csv_body(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'},
    )
