"""Elke overdraagbare soort uit het model heeft een inhoudelijke tegenproef met gelijke aantallen."""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import BigInteger, DateTime, Integer, String, select, text
from sqlalchemy.dialects.postgresql import UUID

from shc_api import migration_inventory as inventory
from shc_api.db import sessionmaker
from shc_api.models import Base

# Deze velden onderscheiden een gewijzigde koppeling of instelling ook bij hetzelfde id.
WIJZIGINGEN = {
    "homes": {"name": "Andere woning"}, "rooms": {"function": "bedroom"},
    "users": {"disabled": True}, "api_clients": {"role": "viewer"},
    "devices": {"calibration": {"offset": 2}, "state": {"group_device_id": str(uuid.uuid4()),
                                                        "bridge_rules": [{"enabled": True}]}}, "integrations": {"enabled": False},
    "logical_roles": {"key": "andere_rol"}, "device_bindings": {"effective_to": datetime.now(UTC)},
    "button_mappings": {"button": "2", "event": "hold", "mode": "lights", "enabled": False,
                        "action": {"steps": [{"type": "light", "action": "toggle", "device_id": str(uuid.uuid4())}]}},
    "climate_zones": {"adaptive_enabled": True, "policy": {"limiet": 19}},
    "schedule_blocks": {"days": [1, 3], "start": "08:30"},
    "comfort_profiles": {"name": "Avond", "schedule": [{"tijd": "09:00"}]}, "adaptive_models": {"params": {"testwaarde": 1}},
    "experiments": {"auto_apply": True, "variants": [1, 2]},
    "commands": {"action": "testactie"}, "connector_changes": {"parameters": {"instelling": 18}},
    "settings": {"value": {"v": {"ingeschakeld": False}}},
    "tariffs": {"import_per_kwh": 1}, "sensor_readings": {"value": 42},
    "energy_hourly": {"samples": 8}, "audit_log": {"action": "testwijziging"},
    "zone_log": {"message": "Andere melding"}, "alerts": {"acknowledged_at": datetime.now(UTC)},
    "backups": {"status": "failed"},
}


def test_volledige_modeldekking():
    assert {table.name for table in inventory.tables()} == set(WIJZIGINGEN)
    assert set(Base.metadata.tables) - set(WIJZIGINGEN) == {
        "sessions", "pairing_codes", "processed_events", "connector_hub_pins", "connector_nonces", "idempotency_keys",
    }


def sleutel(col):
    if isinstance(col.type, UUID):
        return uuid.uuid4()
    if isinstance(col.type, (BigInteger, Integer)):
        return 1
    if isinstance(col.type, DateTime):
        return datetime.now(UTC)
    assert isinstance(col.type, String)
    return "proefinstelling"


@pytest.mark.parametrize("soort", WIJZIGINGEN)
async def test_inhoud_per_soort(soort):
    # PostgreSQL gebruikt dezelfde kolomtypen als in het productmodel. De tijdelijke tabel
    # overschaduwt uitsluitend in deze transactie de app-tabel en heeft geen FK-voorgeschiedenis nodig.
    table = Base.metadata.tables[soort]
    async with sessionmaker()() as db, db.begin():
        ddl = f'CREATE TEMP TABLE "{soort}" ON COMMIT DROP AS TABLE public."{soort}" WITH NO DATA'  # noqa: S608 - model
        await db.execute(text(ddl))
        keys = {col.name: sleutel(col) for col in table.primary_key}
        await db.execute(table.insert().values(**keys))
        conn = await db.connection()
        expected = await conn.run_sync(inventory.fingerprint, table)
        assert expected["readable"] and expected["rows"] == 1
        for field, value in WIJZIGINGEN[soort].items():
            async with db.begin_nested() as change:
                await db.execute(table.update().values(**{field: value}))
                actual = await conn.run_sync(inventory.fingerprint, table, boundary=expected["boundary"])
                assert actual["rows"] == expected["rows"] and actual["sha256"] != expected["sha256"], field
                await change.rollback()
        await db.execute(table.delete())
        assert (await conn.run_sync(inventory.fingerprint, table, boundary=expected["boundary"]))["rows"] == 0
        assert await db.scalar(select(table).limit(1)) is None
