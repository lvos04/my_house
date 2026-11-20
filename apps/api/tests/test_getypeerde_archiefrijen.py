"""Een PostgreSQL-rij behoudt haar type en binaire inhoud gedurende export en herstel."""
import json

import pytest
from sqlalchemy import MetaData, Table, text
from sqlalchemy.ext.asyncio import create_async_engine

from shc_api import backup, migration_inventory, restore_control

from .test_inbedrijfstelling_migratie import sql
from .test_ws_backup_format import PW, temp_db

JSONWAARDEN = [
    "null", '""', '"null"', "0", '"0"', "false", "true", "[]", "{}", '{"veld":null}',
    '{"veld":""}', '{"veld":0}', '{"veld":[]}', '{"veld":{}}', '{"leeg":{}}',
    '[null,"",0,false,{},[]]', "1.23000000000000000001",
]
TIJDSTIPPEN = ["-infinity", "0001-01-01 00:00:00", "2024-02-29 23:59:59.999999", "10000-01-01", "infinity"]
GETALLEN = ["-Infinity", "-1.7976931348623157e308", "-0", "0", "5e-324", "1.7976931348623157e308", "Infinity", "NaN"]
# Alle fysieke typen van de overdraagbare tabellen plus typen die een schema-uitbreiding mag gebruiken.
RANDWAARDEN = {
    "boolean": ["false", "true"],
    "smallint": ["-32768", "0", "32767"],
    "integer": ["-2147483648", "0", "2147483647"],
    "bigint": ["-9223372036854775808", "0", "9223372036854775807"],
    "numeric": ["-Infinity", "-12345678901234567890.001", "0", "0.000", "99999999999999999999.900", "Infinity", "NaN"],
    "real": ["-Infinity", "-3.4028235e38", "-0", "0", "1.40129846e-45", "3.4028235e38", "Infinity", "NaN"],
    "double precision": GETALLEN,
    "text": ["", "0", "null", " ", "e\u0301", "é", "regel\ntwee"],
    "character varying": ["", "0", "null", " ", "é"],
    "uuid": ["00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff"],
    "bytea": [r"\x", r"\x00", r"\xff", r"\x00ff00"],
    "date": ["-infinity", "0001-01-01", "2024-02-29", "10000-01-01", "infinity"],
    "timestamp without time zone": TIJDSTIPPEN,
    "timestamp with time zone": [*TIJDSTIPPEN, "2024-10-27 02:30+02", "2024-10-27 02:30+01"],
    "json": [*JSONWAARDEN, '{ "dubbel" : 1, "dubbel" : 2 }', '{"dubbel":1,"dubbel":2}'],
    "jsonb": JSONWAARDEN,
    "jsonb not null": JSONWAARDEN,
    "jsonb[]": ["{}", "{NULL}", '{"null"}', '{"\\"\\""}', '[0:1]={NULL,"null"}',
                '{NULL,"null"}', '{{NULL,"null"},{"null",NULL}}'],
}
BINAIRE_RIJ = "SELECT encode(record_send(r), 'hex') FROM proefwaarden AS r ORDER BY id"


@pytest.mark.parametrize(("soort", "patronen"), [
    ("float4", ["7fc00000", "7f800001", "ffc00002"]),
    ("float8", ["7ff8000000000000", "7ff0000000000001", "fff8000000000002"]),
])
async def test_nan_met_verschillende_bitpatronen(soort, patronen, tmp_path):
    async with temp_db() as bron, temp_db() as doel:
        for url in (bron, doel):
            await sql(url, f"CREATE TABLE proefwaarden (id int PRIMARY KEY, waarde {soort}, reeks {soort}[])")
        engine = create_async_engine(bron)
        try:
            async with engine.begin() as conn:
                driver = (await conn.get_raw_connection()).driver_connection
                await driver.set_type_codec(soort, schema="pg_catalog", encoder=bytes, decoder=bytes, format="binary")
                tabel = await conn.run_sync(lambda c: Table("proefwaarden", MetaData(), autoload_with=c))
                hashes = set()
                invoer = "INSERT INTO proefwaarden (id, waarde) VALUES ($1, $2)"
                for patroon in patronen:
                    await driver.execute(invoer, 1, bytes.fromhex(patroon))
                    await driver.execute("UPDATE proefwaarden SET reeks = ARRAY[NULL, waarde]")
                    opname = await conn.run_sync(migration_inventory.fingerprint, tabel)
                    assert opname["sha256"] not in hashes
                    hashes.add(opname["sha256"])
                    await conn.execute(tabel.delete())
                await driver.executemany(invoer, [(i, bytes.fromhex(patroon)) for i, patroon in enumerate(patronen)])
                await driver.execute("UPDATE proefwaarden SET reeks = ARRAY[NULL, waarde]")
            async with engine.connect() as conn:
                await backup.write_backup(conn, tmp_path / "waarden.shcbak", PW, True)
        finally:
            await engine.dispose()
        await backup.restore_file(tmp_path / "waarden.shcbak", PW, url=doel)
        assert await sql(doel, BINAIRE_RIJ) == await sql(bron, BINAIRE_RIJ)


@pytest.mark.parametrize("soort", RANDWAARDEN)
async def test_randwaarden_per_kolomtype(soort, tmp_path):
    sqltype = soort.removesuffix(" not null")
    waarden = RANDWAARDEN[soort]
    if soort == sqltype:
        waarden = [None, *waarden]
    archief = tmp_path / "waarden.shcbak"
    async with temp_db() as bron, temp_db() as doel:
        for url in (bron, doel):
            await sql(url, f"CREATE TABLE proefwaarden (id int PRIMARY KEY, waarde {soort})")
        engine = create_async_engine(bron)
        try:
            async with engine.begin() as conn:
                tabel = await conn.run_sync(lambda c: Table("proefwaarden", MetaData(), autoload_with=c))
                schrijf = text(f"INSERT INTO proefwaarden VALUES (:id, CAST(CAST(:waarde AS text) AS {sqltype}))")  # noqa: S608
                hashes = set()
                for waarde in waarden:
                    await conn.execute(schrijf, {"id": 1, "waarde": waarde})
                    rij = await conn.run_sync(migration_inventory.fingerprint, tabel)
                    assert rij["sha256"] not in hashes, (soort, waarde)
                    hashes.add(rij["sha256"])
                    await conn.execute(tabel.delete())
                await conn.execute(schrijf, [{"id": i, "waarde": w} for i, w in enumerate(waarden)])
                verwacht = await conn.run_sync(migration_inventory.fingerprint, tabel)
                bronrijen = (await conn.execute(text(BINAIRE_RIJ))).all()
            async with engine.connect() as conn:
                await backup.write_backup(conn, archief, PW, True)
        finally:
            await engine.dispose()

        await backup.restore_file(archief, PW, url=doel)
        assert await sql(doel, BINAIRE_RIJ) == await sql(bron, BINAIRE_RIJ) == bronrijen
        engine = create_async_engine(doel)
        try:
            async with engine.connect() as conn:
                assert await conn.run_sync(migration_inventory.fingerprint, tabel) == verwacht
        finally:
            await engine.dispose()


async def test_vergelijking_herkent_gewijzigd_kolomtype():
    async with temp_db() as bron, temp_db() as doel:
        for url, soort in ((bron, "integer"), (doel, "bigint")):
            await sql(url, f"CREATE TABLE proefwaarden (id int PRIMARY KEY, waarde {soort})")
            await sql(url, "INSERT INTO proefwaarden VALUES (1, 0)")
        bron_engine, doel_engine = create_async_engine(bron), create_async_engine(doel)
        try:
            async with bron_engine.connect() as a, doel_engine.connect() as b:
                voor = await a.run_sync(migration_inventory.capture)
                verschil = await b.run_sync(migration_inventory.comparison, voor)
                assert verschil["proefwaarden"] == {"source": 1, "target": 1, "same": False}
        finally:
            await bron_engine.dispose()
            await doel_engine.dispose()


async def test_ontbrekende_kolomsleutel_weigert_herstel(monkeypatch, tmp_path):
    voeg_toe = backup._PartWriter.add

    def zonder_kolom(writer, line):
        if writer.table == "proefwaarden":
            rij = json.loads(line)
            del rij["waarde"]
            line = json.dumps(rij).encode() + b"\n"
        voeg_toe(writer, line)

    async with temp_db() as bron, temp_db() as doel:
        for url, waarde in ((bron, None), (doel, "bestaande doelwaarde")):
            await sql(url, "CREATE TABLE proefwaarden (id int PRIMARY KEY, waarde text)")
            await sql(url, "INSERT INTO proefwaarden VALUES (1, :waarde)", {"waarde": waarde})
        with monkeypatch.context() as fout:
            fout.setattr(backup._PartWriter, "add", zonder_kolom)
            kopie = await backup.run_backup(PW, url=bron, backup_dir=tmp_path)
        with pytest.raises(backup.BackupError, match="vastgelegde kolommen"):
            await backup.restore_file(tmp_path / kopie["filename"], PW, url=doel)
        assert await sql(doel, "SELECT waarde FROM proefwaarden") == [("bestaande doelwaarde",)]


async def test_rijvolgorde_zonder_primaire_sleutel(tmp_path):
    async with temp_db() as url:
        await sql(url, "CREATE TABLE proefwaarden (waarde jsonb)")
        engine = create_async_engine(url)
        signatures = []
        try:
            for i, waarden in enumerate(((None, "null"), ("null", None), ("null", "null"))):
                async with engine.begin() as conn:
                    await conn.execute(text("DELETE FROM proefwaarden"))
                    await conn.execute(text("INSERT INTO proefwaarden VALUES (CAST(CAST(:v AS text) AS jsonb))"),
                                       [{"v": v} for v in waarden])
                async with engine.connect() as conn:
                    manifest = await backup.write_backup(conn, tmp_path / f"{i}.shcbak", PW, True)
                    tabel = await conn.run_sync(lambda c: Table("proefwaarden", MetaData(), autoload_with=c))
                    inhoud = await conn.run_sync(migration_inventory.fingerprint, tabel)
                    signatures.append((restore_control._table_signature(manifest, "proefwaarden"), inhoud))
        finally:
            await engine.dispose()
        for origineel, omgekeerd, gewijzigd in zip(*signatures, strict=True):
            assert origineel == omgekeerd != gewijzigd
