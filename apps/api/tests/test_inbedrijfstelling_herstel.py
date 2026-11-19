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
from shc_api.models import BackupRecord, Base

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
    {k: v for k, v in state(False).items() if k != "enabled_at"},
    {**state(False), "enabled_by": 1},
    {**state(False), "enabled_at": state(True)["enabled_at"], "enabled_by": "proefbeheerder"},
    {**state(True), "enabled_at": "geen datum"},
    {**state(True), "enabled_by": ""},
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
    restore_settings = {"commissioning_gate", "maintenance", "safe_mode", "restored_from",
                        "restore_snapshot", "restore_report"}
    # Een oud archief kent deze tabellen nog niet. Een niet-lege tabel blijft een verschil.
    comparable = {name: [] for name in bk.CONNECTOR_LOCAL_TABLES}
    for name, values in rows.items():
        kept = []
        for row in values:
            if name == "settings":
                if row["key"] in restore_settings:
                    continue
                if row["key"] == "backup_state" and isinstance(row["value"].get("v"), dict):
                    value = {k: v for k, v in row["value"]["v"].items() if k not in {"running_since", "running_kind"}}
                    row = {**row, "value": {**row["value"], "v": value}}
            kept.append(json.dumps(row, sort_keys=True).encode())
        comparable[name] = sorted(kept)
    return comparable


@pytest.mark.parametrize("change", ["gewijzigd", "toegevoegd", "verwijderd"])
def test_vergelijking_ziet_gewone_instellingen(change):
    original = [{"key": "proefinstelling", "value": {"v": 1}}]
    changed = {
        "gewijzigd": [{"key": "proefinstelling", "value": {"v": 2}}],
        "toegevoegd": [*original, {"key": "extra", "value": {"v": 1}}],
        "verwijderd": [],
    }[change]
    assert row_bytes({"settings": original}) != row_bytes({"settings": changed})


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
async def test_herstel_gate_en_gewone_instellingen_exact_de_backup(tmp_path, caplog, prior, loaded, enabled, source):
    async with temp_db() as url:
        await seed_and_bytes(url, seed=True)
        await sql(url, "INSERT INTO settings (key,value) VALUES ('proefinstelling', '{\"v\": 1}'::jsonb)")
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


async def test_oud_archief_bewaart_open_backuptaak_en_meldt_onderbreking(tmp_path):
    async with temp_db() as url:
        await seed_and_bytes(url, seed=True)
        await write_gate(url, state(True))
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                task = BackupRecord(kind="export", filename="proefbestand", status="running", detail={"bron": "proef"})
                db.add(task)
                await db.commit()
        finally:
            await engine.dispose()
        rows = await tables(url)
        path = tmp_path / "audit-oude-backuptaak.shcbak"
        path.write_bytes(make_v1(rows, bk.head_revision()))
        assert bk.verify_file(path, PW).manifest["tables"]["backups"]["rows"] == 1
        report = await bk.restore_file(path, PW, url=url)
        after = await tables(url)
        assert row_bytes(after) == row_bytes(rows)
        assert report.as_dict()["interrupted_backups"] == [str(task.id)]
        assert (await sql(url, "SELECT value->'v'->'interrupted_backups' FROM settings WHERE key='restore_report'")) == [
            ([str(task.id)],),
        ]


async def test_opnieuw_exporteren_bewaart_historische_taken_en_slaat_actieve_taak_over(tmp_path):
    async with temp_db() as source, temp_db() as target:
        engine = create_async_engine(source, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                historical = BackupRecord(kind="export", filename="historisch", status="running",
                                          detail={"bron": "oude back-up"})
                completed = BackupRecord(kind="export", filename="afgerond", status="ok",
                                         detail={"bron": "controlerij"})
                db.add_all([historical, completed])
                await db.commit()
            rows = await tables(source)
            v1 = tmp_path / "oud.shcbak"
            v1.write_bytes(make_v1(rows, bk.head_revision()))
            await bk.restore_file(v1, PW, url=source)
            async with AsyncSession(engine) as db:
                db.add(BackupRecord(kind="export", filename="actief", status="running"))
                await db.commit()
            v2 = tmp_path / "nieuw.shcbak"
            async with engine.connect() as conn:
                await bk.write_backup(conn, v2, PW, include_secrets=True)
            verified = bk.verify_file(v2, PW)
            assert verified.format_version == 2
            assert verified.manifest["tables"]["backups"]["rows"] == 2
            report = await bk.restore_file(v2, PW, url=target)
            assert report.interrupted_backups == [str(historical.id)]
            after = await tables(target)
            assert row_bytes(after)["backups"] == row_bytes(rows)["backups"]
        finally:
            await engine.dispose()


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
