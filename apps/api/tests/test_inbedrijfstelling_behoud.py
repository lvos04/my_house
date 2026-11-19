"""Gevulde oude database: bestaande rijen en sequenties bytegelijk op/neer/op."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api.models import (
    AuditLog,
    ButtonMapping,
    ClimateZone,
    Device,
    DeviceBinding,
    Home,
    LogicalRole,
    ScheduleBlock,
    Session,
    User,
)

from .test_ws_backup_format import temp_db


async def seed_and_bytes(url, *, archived=False, seed=False):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            if seed:
                now = datetime.now(UTC)
                uid, hid, lamp, button, role, zone = [uuid.uuid4() for _ in range(6)]
                for model, values in [
                    (User, dict(id=uid, username='proefbeheerder', password_hash='ongeldige-proefhash')),
                    (Session, dict(id='proefsessie', user_id=uid, csrf_token='proef', expires_at=now+timedelta(hours=1))),
                    (Home, dict(id=hid, name='Proefwoning')),
                    (Device, dict(id=lamp, home_id=hid, name='Proeflamp', provider='test', kind='light',
                                  stable_identity='proeflamp', compatibility='tested', tested_at=now,
                                  lifecycle='archived' if archived else 'active')),
                    (Device, dict(id=button, home_id=hid, name='Proefknop', provider='test', kind='button',
                                  stable_identity='proefknop', compatibility='untested',
                                  lifecycle='archived' if archived else 'active')),
                    (LogicalRole, dict(id=role, home_id=hid, key='proef', label='Proefrol')),
                    (DeviceBinding, dict(role_id=role, device_id=lamp)),
                    (ClimateZone, dict(id=zone, home_id=hid, name='Proefzone', unit_device_id=lamp)),
                    (ScheduleBlock, dict(zone_id=zone, days=[0, 2], start='08:00', target_c=20)),
                    (ButtonMapping, dict(home_id=hid, device_id=button, button='proef', event='short_release',
                                         mode='lights', action={'steps': [{'device_id':str(lamp), 'action':'toggle'}]})),
                    (AuditLog, dict(actor='proefbeheerder', action='device_marked_tested',
                                    target='Proeflamp', detail={'device':str(lamp), 'home':str(hid)})),
                ]:
                    await conn.execute(model.__table__.insert().values(**values))
            tables = (await conn.execute(text("""
                SELECT tablename FROM pg_tables WHERE schemaname='public'
                AND tablename<>'alembic_version' ORDER BY tablename
            """))).scalars().all()
            # Deze tabellen ontbreken in 0004. Op het nieuwe schema moeten ze leeg zijn.
            result = {name: [] for name in bk.CONNECTOR_LOCAL_TABLES}
            for table in tables:
                where = "WHERE t.key <> 'commissioning_gate'" if table == 'settings' else ''
                # Tabelnamen komen uitsluitend uit de eigen catalogus, geen verzoekinvoer.
                row = "to_jsonb(t)"
                if table == "devices":
                    row += " - 'detection_method' - 'fallback'"
                if table in {"devices", "climate_zones"}:
                    # 0007 voegt alleen versiemetadata toe. Controleer de startwaarde apart
                    # en vergelijk daarna alle velden die de oude installatie kon opslaan.
                    versions = (await conn.execute(text(
                        f"""SELECT to_jsonb(t)->'connector_version' FROM "{table}" t"""  # noqa: S608
                    ))).scalars().all()
                    revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                    assert all(version == (1 if revision == "0007" else None) for version in versions)
                    row += " - 'connector_version'"
                rows = (await conn.execute(text(
                    f'''SELECT ({row})::text FROM "{table}" t {where}'''  # noqa: S608
                ))).scalars().all()
                result[table] = sorted(rows)
            result['sequences'] = [tuple(row) for row in (await conn.execute(text(
                "SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
            ))).all()]
            return json.dumps(result, sort_keys=True).encode()
    finally:
        await engine.dispose()


@pytest.mark.parametrize('archived', [False, True])
async def test_migratie_gebruikers_sessies_apparaten_zones_koppelingen_planningen_bytegelijk(archived):
    async with temp_db('0004') as url:
        before = await seed_and_bytes(url, archived=archived, seed=True)
        for revision in ['head', '0004', 'head']:
            method = command.downgrade if revision == '0004' else command.upgrade
            await asyncio.to_thread(method, bk.alembic_config(url), revision)
            assert await seed_and_bytes(url) == before, 'Migratie veranderde bestaande data of sequenties'
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE homes SET name='Bewuste controlewijziging'"))
            assert await seed_and_bytes(url) != before, 'Bytevergelijking moet een echte wijziging zien'
        finally:
            await engine.dispose()
