"""De gatetoestand overleeft herstel zonder de teruggezette gegevens te veranderen."""

import json
import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api.commissioning import gate_state, read_gate
from shc_api.models import Base

from .test_inbedrijfstelling_behoud import seed_and_bytes
from .test_inbedrijfstelling_migratie import sql
from .test_ws_backup_format import PW, make_v1, temp_db


def state(enabled):
    return {
        "version": 1,
        "enabled": enabled,
        "origin": "existing_devices",
        "enabled_at": "2026-11-18T00:00:00+00:00" if enabled else None,
        "enabled_by": "proefbeheerder" if enabled else None,
    }


@pytest.mark.parametrize("bad", [
    None, [], "onleesbaar", {}, {**state(False), "version": True},
    {**state(False), "enabled": 0}, {**state(False), "origin": []},
    {**state(False), "origin": "fresh"}, {**state(False), "origin": "onbekend"},
])
def test_onbekende_gate_is_dicht(bad):
    assert gate_state(bad)["enabled"] is True
    assert gate_state(bad)["known"] is False


async def write_gate(url, value):
    if value is None:
        await sql(url, "DELETE FROM settings WHERE key='commissioning_gate'")
    else:
        await sql(url, """
            INSERT INTO settings (key,value) VALUES ('commissioning_gate',CAST(:v AS jsonb))
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
        """, {"v": json.dumps({"v": value})})


async def tables(url):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            names = (await conn.execute(text("""
                SELECT tablename FROM pg_tables WHERE schemaname='public'
                AND tablename<>'alembic_version' ORDER BY tablename
            """))).scalars().all()
            order = {table.name: index for index, table in enumerate(Base.metadata.sorted_tables)}
            result = {}
            for name in sorted(names, key=lambda name: order[name]):
                result[name] = (await conn.execute(text(
                    f'SELECT to_jsonb(t) FROM "{name}" t'  # noqa: S608 - eigen catalogus
                ))).scalars().all()
            return result
    finally:
        await engine.dispose()


def row_bytes(rows):
    return {name: sorted(json.dumps(row, sort_keys=True).encode() for row in values)
            for name, values in rows.items() if name != "settings"}


@pytest.mark.parametrize("prior,loaded,enabled,source", [
    (True, False, True, "before"),
    (True, True, True, "before"),
    (False, True, True, "backup"),
    (False, False, False, "backup"),
    (None, False, True, "before"),
    ("ongeldig", False, True, "before"),
    (False, None, True, "backup"),
    (False, "ongeldig", True, "backup"),
])
async def test_herstel_gate_en_buiten_settings_exact_de_backup(tmp_path, caplog, prior, loaded, enabled, source):
    async with temp_db() as url:
        await seed_and_bytes(url, seed=True)
        await write_gate(url, state(loaded) if type(loaded) is bool else loaded)
        backup_rows = await tables(url)
        path = tmp_path / "inbedrijfstelling.shcbak"
        path.write_bytes(make_v1(backup_rows, bk.head_revision()))
        prior_value = state(prior) if type(prior) is bool else prior
        await write_gate(url, prior_value)
        started = datetime.now(UTC)
        with caplog.at_level(logging.INFO, logger="shc_api.backup"):
            await bk.restore_file(path, PW, url=url)
        finished = datetime.now(UTC)
        assert row_bytes(await tables(url)) == row_bytes(backup_rows)
        value = (await sql(url, "SELECT value->'v' FROM settings WHERE key='commissioning_gate'"))[0][0]
        assert value["enabled"] is enabled
        restored = value["restored"]
        assert restored["source"] == source
        assert restored["enabled_before"] is (prior is not False)
        assert restored["known_before"] is (type(prior) is bool)
        assert restored["enabled_loaded"] is (loaded is not False)
        assert restored["known_loaded"] is (type(loaded) is bool)
        assert restored["mode"] == "same_home"
        assert started <= datetime.fromisoformat(restored["at"]) <= finished
        if prior is True:
            assert {k: value[k] for k in prior_value} == prior_value
        assert "Inbedrijfstellingsgate na terugzetten" in caplog.text


@pytest.mark.parametrize("prior_revision", ["0004", "head"])
@pytest.mark.parametrize("mode", ["same_home", "new_home"])
async def test_oude_backup_behoudt_eerder_aan_ook_bij_nieuwe_woning(tmp_path, prior_revision, mode):
    async with temp_db("0004") as old:
        await seed_and_bytes(old, seed=True)
        rows = await tables(old)
    path = tmp_path / "oude-inbedrijfstelling.shcbak"
    path.write_bytes(make_v1(rows, "0004"))
    async with temp_db(prior_revision) as url:
        await bk.restore_file(path, PW, url=url, mode=mode)
        value = (await sql(url, "SELECT value->'v' FROM settings WHERE key='commissioning_gate'"))[0][0]
        # Vóór invoering mag de eenmalige migratie de bestaande apparaten herkennen.
        assert value["enabled"] is (prior_revision == "head")
        assert value["restored"]["mode"] == mode
        assert value["restored"]["source"] == ("before" if prior_revision == "head" else "backup")
        if mode == "same_home":
            restored = await tables(url)
            for row in restored["devices"]:
                assert row.pop("detection_method") is None
                assert row.pop("fallback") is None
            assert row_bytes(restored) == row_bytes(rows)


async def test_herstel_in_lege_database_geeft_geen_dubbele_initialisatierij(tmp_path):
    async with temp_db() as source:
        await seed_and_bytes(source, seed=True)
        await write_gate(source, state(False))
        rows = await tables(source)
    path = tmp_path / "lege-database.shcbak"
    path.write_bytes(make_v1(rows, bk.head_revision()))
    async with temp_db(None) as url:
        await bk.restore_file(path, PW, url=url)
        assert row_bytes(await tables(url)) == row_bytes(rows)
        value = (await sql(url, "SELECT value->'v' FROM settings WHERE key='commissioning_gate'"))[0][0]
        assert value["enabled"] is False
        assert value["restored"]["enabled_before"] is None


async def test_gate_leest_verse_instelling_zonder_automatisch_te_schrijven():
    async with temp_db() as url:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as db:
                assert (await read_gate(db))["enabled"] is True
                await write_gate(url, state(False))
                assert (await read_gate(db))["enabled"] is False
                await write_gate(url, None)
                assert (await read_gate(db))["enabled"] is True
                assert (await read_gate(db))["known"] is False
                assert await sql(url, "SELECT count(*) FROM settings WHERE key='commissioning_gate'") == [(0,)]
        finally:
            await engine.dispose()


async def test_mislukt_herstel_behoudt_hele_database_en_logt_geen_behoud(tmp_path, monkeypatch, caplog):
    async with temp_db() as url:
        await seed_and_bytes(url, seed=True)
        rows = await tables(url)
        before_bytes = await seed_and_bytes(url)
        path = tmp_path / "rollback.shcbak"
        path.write_bytes(make_v1(rows, bk.head_revision()))
        original = bk._after_restore

        def fail(*args):
            original(*args)
            raise RuntimeError("Bewuste fout na de herstelbeslissing")

        monkeypatch.setattr(bk, "_after_restore", fail)
        with caplog.at_level(logging.INFO, logger="shc_api.backup"):
            with pytest.raises(RuntimeError, match="Bewuste fout"):
                await bk.restore_file(path, PW, url=url)
        assert await tables(url) == rows
        assert await seed_and_bytes(url) == before_bytes
        assert "Inbedrijfstellingsgate na terugzetten" not in caplog.text
