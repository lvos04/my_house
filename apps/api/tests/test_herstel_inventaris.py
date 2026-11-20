"""Elke overdraagbare soort uit het model heeft een inhoudelijke tegenproef met gelijke aantallen."""
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import BigInteger, DateTime, Integer, String, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from shc_api import backup, migration_source
from shc_api import migration_inventory as inventory
from shc_api.config import get_settings
from shc_api.db import engine, sessionmaker
from shc_api.models import Base
from shc_api.security import SecretBox

from .test_herstelpoorten import proefinstallatie
from .test_ws_backup_format import PW

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


async def test_dekking_van_het_databaseschema(monkeypatch):
    def alle_kolommen(conn):
        catalogus = inspect(conn)
        return {(t if s == "public" else f"{s}.{t}"): cols for schema in catalogus.get_schema_names()
                if schema != "information_schema" and not schema.startswith("pg_")
                for (s, t), cols in catalogus.get_multi_columns(schema=schema).items()}

    async with proefinstallatie(monkeypatch), sessionmaker()() as db:
        await migration_source.login_journal(db)
        conn = await db.connection()
        schema = await conn.run_sync(alle_kolommen)
        verwacht = await conn.run_sync(inventory.capture)
        lokaal = {*backup.LOCAL_TABLES, "alembic_version", *(f"{s}.{t}" for s, t in inventory.LOCAL_JOURNALS)}
        assert set(verwacht) == set(schema) - lokaal
        assert all(backup.LOCAL_TABLES.values()) and all(inventory.LOCAL_JOURNALS.values())
        for name, item in verwacht.items():
            kolommen = {c["name"] for c in schema[name]}
            vluchtig = inventory.RUNTIME_COLUMNS.get(name, {})
            assert set(item["columns"]).isdisjoint(vluchtig)
            assert set(item["columns"]) | set(vluchtig) == kolommen
            assert all(vluchtig.values())
            assert all(c["name"] in item["columns"] for c in schema[name] if isinstance(c["type"], JSONB))

        # Ook bij een lege tabel mag een verschil tussen de werkelijke database en de app geen groen geven.
        await db.commit()  # De servercursors van de eerste vergelijking sluiten vóór de DDL.
        conn = await db.connection()
        await db.execute(text("CREATE SCHEMA uitbreiding"))
        await db.execute(text("CREATE TABLE uitbreiding.proef (id serial PRIMARY KEY, inhoud jsonb)"))
        await db.execute(text("ALTER TABLE devices ADD COLUMN nieuwe_proefkolom jsonb"))
        verschil = await conn.run_sync(inventory.comparison, verwacht)
        assert not verschil["devices"]["same"] and not verschil["uitbreiding.proef"]["same"]
        await db.execute(text("""INSERT INTO uitbreiding.proef VALUES (1, '{"deel": {"bewaren": true}}')"""))
        nieuw = await conn.run_sync(inventory.capture)
        assert "nieuwe_proefkolom" in nieuw["devices"]["columns"]
        assert nieuw["uitbreiding.proef"]["columns"] == ["id", "inhoud"]
        assert all(r["same"] for r in (await conn.run_sync(inventory.comparison, nieuw)).values())
        await db.execute(text("UPDATE uitbreiding.proef SET inhoud = inhoud #- '{deel,bewaren}'"))
        assert not (await conn.run_sync(inventory.comparison, nieuw))["uitbreiding.proef"]["same"]
        await db.rollback()


async def test_extra_schema_in_back_up_en_herstel(monkeypatch, tmp_path):
    archief = tmp_path / "extra.shcbak"
    async with proefinstallatie(monkeypatch):
        async with engine().begin() as conn:
            await conn.execute(text("CREATE SCHEMA uitbreiding"))
            await conn.execute(text("CREATE TABLE uitbreiding.proef (id serial PRIMARY KEY, inhoud jsonb)"))
            await conn.execute(text("""INSERT INTO uitbreiding.proef VALUES (7, '{"bewaren": [true]}')"""))
        async with engine().connect() as conn:
            manifest = await backup.write_backup(conn, archief, PW, True)
        assert manifest["tables"]["uitbreiding.proef"]["rows"] == 1
        async with engine().begin() as conn:
            await conn.execute(text("DELETE FROM uitbreiding.proef"))
        await backup.restore_file(archief, PW)
        async with engine().begin() as conn:
            assert await conn.scalar(text("SELECT inhoud FROM uitbreiding.proef WHERE id=7")) == {"bewaren": [True]}
            assert await conn.scalar(text("INSERT INTO uitbreiding.proef DEFAULT VALUES RETURNING id")) == 8


def test_duurzame_buren_van_vluchtige_jsonvelden():
    for payload in ({"v": {"loops": {"peilen": {"last": "nu", "beheernotitie": []}}}},
                    {"v": {"loops": {"peilen": {"last": "nu"}}}, "onbekend": {"leeg": {}}}):
        assert inventory._setting_has_data({"key": "worker_loops", "value": payload})
    assert not inventory._setting_has_data({"key": "worker_loops", "value": {"v": {"loops": {"peilen": {"last": "nu"}}}}})
    stand = {"state": {"last_event": {"at": "nu", "extra": []}},
             "diagnostics": {"inventory": {"seen_at": "nu", "nieuw": {"genest": {}}}}}
    for path in inventory.RUNTIME_PATHS["devices"]:
        inventory._omit_live_leaf(stand, path)
    assert stand == {"state": {"last_event": {"extra": []}}, "diagnostics": {"inventory": {"nieuw": {"genest": {}}}}}


def andere_waarde(kolom, oud):
    if isinstance(kolom.type, JSONB):
        return {"nieuw_veld": {"lijst": [{"bewaren": True}]}}
    soort = kolom.type.python_type
    if soort is bytes:
        return SecretBox(get_settings().resolved_secret_key()).encrypt({"proef": "andere waarde"})
    if soort is bool:
        return not oud
    if soort in (int, float, Decimal):
        return (oud or 0) + 1
    if soort is uuid.UUID:
        return uuid.uuid4()
    if soort is datetime:
        return (oud or datetime.now(UTC)) + timedelta(days=1)
    if soort is date:
        return (oud or date.today()) + timedelta(days=1)
    assert soort is str, f"Voeg een proefwaarde toe voor {kolom}"
    return "y" if oud == "x" else "x"


@pytest.mark.parametrize("soort", [t.name for t in inventory.tables()])
async def test_iedere_overdraagbare_kolom(soort):
    tabel = Base.metadata.tables[soort]
    async with sessionmaker()() as db, db.begin():
        await db.execute(text(f'CREATE TEMP TABLE "{soort}" ON COMMIT DROP AS TABLE public."{soort}" WITH NO DATA'))  # noqa: S608
        await db.execute(tabel.insert().values(**{c.name: sleutel(c) for c in tabel.primary_key}))
        rij = (await db.execute(select(tabel))).mappings().one()
        conn = await db.connection()
        basis = await conn.run_sync(inventory.fingerprint, tabel)
        for kolom in tabel.c:
            async with db.begin_nested() as wijziging:
                await db.execute(tabel.update().values({kolom: andere_waarde(kolom, rij[kolom.name])}))
                na = await conn.run_sync(inventory.fingerprint, tabel)
                uitgesloten = kolom.name in inventory.RUNTIME_COLUMNS.get(soort, {})
                assert (na["sha256"] == basis["sha256"]) == uitgesloten, str(kolom)
                await wijziging.rollback()
