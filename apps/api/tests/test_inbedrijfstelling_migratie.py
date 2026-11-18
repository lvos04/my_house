"""Inbedrijfstelling: behoud van bestaande rijen en een blijvend ingeschakelde gate."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk

from .test_ws_backup_format import temp_db


async def sql(url, statement, params=None):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(statement), params or {})
            return result.all() if result.returns_rows else []
    finally:
        await engine.dispose()


@pytest.mark.parametrize("archived", [False, True])
async def test_migratie_0005_bestaand_op_neer_op_bewaart_apparaat(archived):
    async with temp_db("0004") as url:
        await sql(
            url,
            """
            INSERT INTO homes (id,name,timezone,currency,active_from) VALUES
              ('10000000-0000-0000-0000-000000000001','Proefwoning','UTC','EUR',now());
        """,
        )
        await sql(
            url,
            """
            INSERT INTO devices (id,home_id,provider,kind,name,stable_identity,capabilities,
               compatibility,lifecycle,online,state,discovered_at,calibration,diagnostics,tested_at)
            VALUES ('10000000-0000-0000-0000-000000000002',
              '10000000-0000-0000-0000-000000000001','test','light','Proeflamp','proeflamp','[]',
              'tested',:lifecycle,false,'{}',now(),'{}','{}',now())
        """,
            {"lifecycle": "archived" if archived else "active"},
        )
        before = await sql(url, "SELECT row_to_json(d)::text FROM devices d")
        for revision in ["head", "0004", "head"]:
            if revision == "0004":
                await asyncio.to_thread(command.downgrade, bk.alembic_config(url), revision)
            else:
                await asyncio.to_thread(command.upgrade, bk.alembic_config(url), revision)
            after = await sql(url, "SELECT (to_jsonb(d) - 'detection_method' - 'fallback')::text FROM devices d")
            assert json.loads(before[0][0]) == json.loads(after[0][0])
        value = (await sql(url, "SELECT value->'v' FROM settings WHERE key='commissioning_gate'"))[0][0]
        assert value["enabled"] is False and value["origin"] == "existing_devices"
        value.update(enabled=True, enabled_at="2026-11-18T00:00:00+00:00", enabled_by="proefbeheerder")
        await sql(
            url,
            "UPDATE settings SET value=CAST(:v AS jsonb) WHERE key='commissioning_gate'",
            {"v": json.dumps({"v": value})},
        )
        await asyncio.to_thread(command.downgrade, bk.alembic_config(url), "0004")
        await asyncio.to_thread(command.upgrade, bk.alembic_config(url), "head")
        assert (await sql(url, "SELECT value->'v' FROM settings WHERE key='commissioning_gate'"))[0][0] == value


async def test_migratie_0005_nieuwe_installatie_begint_aan():
    async with temp_db("head") as url:
        value = (await sql(url, "SELECT value->'v' FROM settings WHERE key='commissioning_gate'"))[0][0]
        assert value["enabled"] is True and value["origin"] == "fresh"
        assert value["enabled_at"] and value["enabled_by"]


@pytest.mark.parametrize("value", [
    {"version": 1, "enabled": True, "origin": "fresh", "enabled_at": "eerder", "enabled_by": "beheerder"},
    {"version": 1, "enabled": False, "origin": "existing_devices", "enabled_at": None, "enabled_by": None},
    {"version": 99, "enabled": False},
])
async def test_migratie_bewaart_bestaande_gate_integraal(value):
    async with temp_db("0004") as url:
        await sql(url, "INSERT INTO settings (key,value) VALUES ('commissioning_gate',CAST(:v AS jsonb))",
                  {"v": json.dumps({"v": value})})
        before = await sql(url, "SELECT value::text FROM settings WHERE key='commissioning_gate'")
        await asyncio.to_thread(command.upgrade, bk.alembic_config(url), "head")
        assert await sql(url, "SELECT value::text FROM settings WHERE key='commissioning_gate'") == before


async def test_migratie_tijd_is_utc_ook_in_andere_databasetijdzone():
    async with temp_db("0004") as url:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            started = datetime.now(UTC)
            async with engine.begin() as conn:
                await conn.execute(text("SET LOCAL TIME ZONE 'Europe/Amsterdam'"))
                await conn.run_sync(lambda c: command.upgrade(bk.alembic_config(connection=c), "head"))
            finished = datetime.now(UTC)
        finally:
            await engine.dispose()
        value = (await sql(url, "SELECT value->'v' FROM settings WHERE key='commissioning_gate'"))[0][0]
        at = datetime.fromisoformat(value["enabled_at"])
        assert at.utcoffset().total_seconds() == 0
        assert started <= at <= finished


async def test_nieuwe_kolommen_zijn_nullable_en_detectiemethode_is_begrensd():
    async with temp_db("0004") as url:
        await sql(url, """
            INSERT INTO homes (id,name,timezone,currency,active_from)
            VALUES ('10000000-0000-0000-0000-000000000001','Proefwoning','UTC','EUR',now())
        """)
        await sql(url, """
            INSERT INTO devices (id,home_id,provider,kind,name,stable_identity,capabilities,
                compatibility,lifecycle,online,state,discovered_at,calibration,diagnostics)
            VALUES ('10000000-0000-0000-0000-000000000002',
                '10000000-0000-0000-0000-000000000001','test','light','Proeflamp','proef','[]',
                'untested','active',false,'{}',now(),'{}','{}')
        """)
        await asyncio.to_thread(command.upgrade, bk.alembic_config(url), "head")
        assert await sql(url, "SELECT detection_method,fallback FROM devices") == [(None, None)]
        for method in ["scan", "manual"]:
            await sql(url, "UPDATE devices SET detection_method=:m,fallback='Fabrikantbediening'", {"m": method})
            assert await sql(url, "SELECT detection_method,fallback FROM devices") == [(method, "Fabrikantbediening")]
        with pytest.raises(IntegrityError, match="ck_devices_detection_method"):
            await sql(url, "UPDATE devices SET detection_method='onbekend'")
        assert await sql(url, "SELECT detection_method FROM devices") == [("manual",)]
