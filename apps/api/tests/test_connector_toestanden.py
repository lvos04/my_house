"""Bewijs van apparaatstanden bij onderbreking, herstel van een dump en versiewissels."""

import asyncio
import subprocess
import uuid
from datetime import timedelta

import pytest
from alembic import command

from shc_api import backup as bk
from shc_api import cli
from shc_api.config import get_settings
from shc_api.connector_changes import digest
from shc_api.db import sessionmaker
from shc_api.models import CommandRecord, Device, utcnow
from shc_api.worker import Worker, core

from .test_connector_herstel import _apply, _queue_rollback
from .test_connector_routes import proef as proef
from .test_inbedrijfstelling_migratie import sql
from .test_ws_backup_format import PW, temp_db


@pytest.fixture
async def manual_dump(tmp_path, monkeypatch):
    url = get_settings().database_url
    name = bk.dump_filename(False)
    monkeypatch.setattr(bk, "dump_filename", lambda auto: name)

    async def create():
        return await bk.run_db_dump(PW, auto=False, url=url, backup_dir=tmp_path)

    yield create
    # Ook een geweigerde dump heeft een eigen registratierij.
    await sql(url, "DELETE FROM backups WHERE filename=:name", {"name": name})


@pytest.mark.parametrize("field", ["WdSpd", "Quiet"])
async def test_vastgestelde_bedienvelden_blijven_verplicht(proef, monkeypatch, field):
    adapter = next(iter(proef.worker.adapters.values()))
    update, *_ = await adapter.poll()
    assert update.state["caps"]["fan"]
    async with sessionmaker()() as db:
        device = await db.get(Device, proef.device_id)
        device.state = {**device.state, **update.state}
        await db.commit()
    original = proef.unit._reply

    def reply(inner, *args, **kwargs):
        if inner["t"] == "dat":
            idx = inner["cols"].index(field)
            inner["cols"].pop(idx)
            inner["dat"].pop(idx)
        return original(inner, *args, **kwargs)

    monkeypatch.setattr(proef.unit, "_reply", reply)
    preview = await proef.propose()
    assert preview["reason_code"] == "readback_unavailable" and preview["rollback_recipe"] is None
    assert (await proef.post("apply", proef.request(preview=preview))).status_code == 409
    assert await proef.count() == 0 and not proef.unit.commands


@pytest.mark.parametrize("intervening", [False, True])
async def test_onderbroken_claim_sluit_met_verse_teruglezing(proef, monkeypatch, intervening):
    preview = await proef.propose()
    receipt = await proef.post("apply", proef.request(preview=preview))
    apply_id = uuid.UUID(receipt.json()["command_id"])
    assert (await proef.worker._claim_next({proef.home_id}))[0] == apply_id
    async with sessionmaker()() as db:
        cmd = await db.get(CommandRecord, apply_id)
        cmd.updated_at = utcnow() - timedelta(minutes=3)
        await db.commit()
    restarted = Worker()
    restarted.adapters.update(proef.worker.adapters)
    await restarted.process_commands({proef.home_id})
    evidence = await proef.evidence(str(apply_id))
    assert evidence["recovery_required"] and not proef.unit.commands
    recovery = evidence["rollback_preview"]
    assert recovery["diff"] == {"before": preview["before"], "after": preview["before"]}
    with monkeypatch.context() as patch:
        reply = proef.unit._reply

        def incomplete(inner, *args, **kwargs):
            if inner["t"] == "dat":
                index = inner["cols"].index("WdSpd")
                inner["cols"].pop(index)
                inner["dat"].pop(index)
            return reply(inner, *args, **kwargs)

        patch.setattr(proef.unit, "_reply", incomplete)
        unreadable = await proef.evidence(str(apply_id))
        assert unreadable["rollback_preview"] is None
        assert unreadable["rollback_preview_reason_code"] == "readback_unavailable"
        refused = await proef.post("rollback", proef.request(preview=preview, rollback=recovery))
        assert refused.status_code == 409 and refused.json()["code"] == "readback_unavailable"
        assert not proef.unit.commands
    approval = proef.request(preview=preview, rollback=recovery)
    if intervening:
        proef.key_id = str(uuid.uuid4())
        await _apply(proef)
    result = await proef.post("rollback", approval)
    if intervening:
        assert result.status_code == 409 and len(proef.unit.commands) == 1
        assert (await proef.evidence(str(apply_id)))["recovery_required"]
        return
    assert result.status_code == 202
    await restarted.process_commands({proef.home_id})
    done = await proef.evidence(result.json()["command_id"])
    assert done["rollback_status"] == "confirmed" and done["rollback_after"] == done["before"]
    assert done["rollback_after_observed_at"]
    assert not done["recovery_required"] and not proef.unit.commands


@pytest.mark.parametrize("missing", ["bewijsrij", "vorige_stand", "herstelstatus", "herstelstand"])
async def test_dump_met_gebroken_opdrachtbewijs_wordt_niet_vrijgegeven(proef, tmp_path, manual_dump, missing):
    preview, _ = await _apply(proef)
    await _queue_rollback(proef, preview)
    url = get_settings().database_url
    mutations = {
        "bewijsrij": "DELETE FROM connector_changes WHERE id=:id",
        "vorige_stand": "UPDATE connector_changes SET before='{}' WHERE id=:id",
        "herstelstand": "UPDATE connector_changes SET rollback_from = jsonb_build_object('state', '{}'::jsonb) WHERE id=:id",
        "herstelstatus": "UPDATE connector_changes SET rollback_status=NULL WHERE id=:id",
    }
    await sql(url, mutations[missing], {"id": uuid.UUID(preview["proposal_id"])})
    with pytest.raises(bk.BackupError, match="connectorherstelbewijs"):
        await manual_dump()
    assert not list(tmp_path.glob("*.shcdump"))
    encrypted = tmp_path / "eerder.shcdump"
    await asyncio.to_thread(bk._pg_dump_sync, url, encrypted, PW, utcnow().isoformat())
    out = tmp_path / "bestaand.pgdump"
    out.write_bytes(b"eerder gecontroleerd bestand")
    with pytest.raises(bk.BackupError, match="connectorherstelbewijs"):
        await asyncio.to_thread(bk.decrypt_dump, encrypted, PW, out)
    assert out.read_bytes() == b"eerder gecontroleerd bestand"


async def test_twee_schematerugvallen_met_dump_op_0006(proef, tmp_path):
    preview, _ = await _apply(proef)
    await _queue_rollback(proef, preview)
    await proef.work()
    proof = await proef.evidence(preview["proposal_id"])
    assert proof["rollback_status"] == "confirmed" and proof["before"] == proof["rollback_after"]
    url = get_settings().database_url
    row = "SELECT to_jsonb(c)::text FROM connector_changes c WHERE id=:id"
    key = {"id": uuid.UUID(preview["proposal_id"])}
    before = await sql(url, row, key)
    # Gebruik een kopie, zodat de actieve fixtures hun oorspronkelijke schema behouden.
    async with temp_db(None) as copy, temp_db(None) as restored:
        async def roundtrip(source, dest):
            path, plain = tmp_path / "schema.shcdump", tmp_path / "schema.pgdump"
            await asyncio.to_thread(bk._pg_dump_sync, source, path, PW, utcnow().isoformat())
            await asyncio.to_thread(bk.decrypt_dump, path, PW, plain)
            await asyncio.to_thread(subprocess.run, ["pg_restore", "--single-transaction", "--dbname",
                                    cli._libpq(dest)[0], str(plain)], check=True, capture_output=True)

        await roundtrip(url, copy)
        await asyncio.to_thread(command.downgrade, bk.alembic_config(copy), "0006")
        await roundtrip(copy, restored)
        cfg = bk.alembic_config(restored)
        for _ in range(2):
            await asyncio.to_thread(command.upgrade, cfg, "0007")
            assert await sql(restored, row, key) == before
            confirmed = "SELECT count(*) FROM commands WHERE source='connector' AND status='confirmed'"
            assert await sql(restored, confirmed) == [(2,)]
            await asyncio.to_thread(command.downgrade, cfg, "0006")
            assert cli.current_revision(restored) == "0006"


@pytest.mark.parametrize("vorige_versie", ["beperkte_teruglezing", "volledige_teruglezing"])
async def test_oude_wachtende_rollback_blokkeert_ook_bij_gelijke_schemaversie(
    proef, tmp_path, monkeypatch, vorige_versie,
):
    preview, _ = await _apply(proef)
    rollback_id = await _queue_rollback(proef, preview)
    url = get_settings().database_url
    key = {"id": uuid.UUID(preview["proposal_id"])}
    if vorige_versie == "beperkte_teruglezing":
        await sql(url, """
            UPDATE connector_changes SET before=before-'controls', rollback_from=rollback_from->'state' WHERE id=:id
        """, key)
    else:
        await sql(url, "UPDATE idempotency_keys SET scope='connector:voorstel' WHERE key=:id", key)
    with pytest.raises(cli.MigrationError, match="vorige installatie"):
        await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
    assert not list(tmp_path.iterdir())
    entered = asyncio.Event()
    check = cli.check_connector_transition

    async def observed(*args):
        try:
            await check(*args)
        finally:
            entered.set()

    async def forbidden(_worker):
        pytest.fail("De nieuwe worker start terwijl de vorige versie nog moet herstellen.")

    monkeypatch.setattr(cli, "check_connector_transition", observed)
    monkeypatch.setattr(core.Worker, "run", forbidden)
    task = asyncio.create_task(core.run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert await sql(url, "SELECT status FROM commands WHERE id=:id", {"id": uuid.UUID(rollback_id)}) == [("queued",)]
    assert len(proef.unit.commands) == 1 and proef.unit.state["SetTem"] == 22


@pytest.mark.parametrize("rollback", [False, True])
async def test_onderbreking_na_echte_simulatorverzending(proef, tmp_path, monkeypatch, manual_dump, rollback):
    preview = await proef.propose()
    receipt = await proef.post("apply", proef.request(preview=preview))
    cmd_id = receipt.json()["command_id"]
    if rollback:
        await proef.work()
        cmd_id = await _queue_rollback(proef, preview)

    async def interrupt(*args):
        # Het datagram is verwerkt. Het proces valt uit vóór de uitkomst wordt gecommit.
        raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr(proef.worker, "_record_result", interrupt)
        with pytest.raises(asyncio.CancelledError):
            await proef.work()
    sent = 2 if rollback else 1
    assert len(proef.unit.commands) == sent
    assert proef.unit.state["SetTem"] == (20 if rollback else 22)
    url = get_settings().database_url
    key = {"id": uuid.UUID(cmd_id)}
    assert await sql(url, "SELECT status FROM commands WHERE id=:id", key) == [("sending",)]
    record = await manual_dump()
    plain = tmp_path / "onderbroken.pgdump"
    await asyncio.to_thread(bk.decrypt_dump, tmp_path / record["filename"], PW, plain)
    async with temp_db(None) as copy:
        await asyncio.to_thread(subprocess.run, ["pg_restore", "--single-transaction", "--dbname",
                                cli._libpq(copy)[0], str(plain)], check=True, capture_output=True)
        assert await sql(copy, "SELECT status FROM commands WHERE id=:id", key) == [("sending",)]
        assert await sql(copy, "SELECT before FROM connector_changes WHERE :id IN (command_id, rollback_command_id)",
                         key) == [(preview["before"],)]
        assert await sql(copy, "SELECT count(*) FROM connector_hub_pins") == [(0,)]
    await sql(url, "UPDATE commands SET updated_at=now()-interval '3 minutes' WHERE id=:id", key)
    restarted = Worker()
    restarted.adapters.update(proef.worker.adapters)
    await restarted.process_commands({proef.home_id})
    proof = await proef.evidence(cmd_id)
    assert proof["recovery_required"] and len(proef.unit.commands) == sent
    assert proof["rollback_preview"]["diff"]["before"]["target_c"] == proef.unit.state["SetTem"]
    recovered = await _queue_rollback(proef, preview)
    await restarted.process_commands({proef.home_id})
    done = await proef.evidence(recovered)
    assert done["rollback_status"] == "confirmed" and done["before"] == done["rollback_after"]
    assert not done["recovery_required"] and len(proef.unit.commands) == 2


@pytest.mark.parametrize(("settled", "historical_hash"), [(True, True), (False, True), (True, False)])
async def test_oud_opdrachtpaar_alleen_als_afgehandelde_historie(proef, manual_dump, settled, historical_hash):
    preview, _ = await _apply(proef)
    rollback_id = await _queue_rollback(proef, preview)
    if settled:
        await proef.work()
    url = get_settings().database_url
    # De oudste preview bevatte geen richting en mat alleen voeding, modus en doeltemperatuur.
    # Reproduceer het oude formaat inclusief zijn eigen hash, zodat een echt verloren veld detecteerbaar blijft.
    old_before = {key: value for key, value in preview["before"].items() if key != "controls"}
    old_hash = digest({
        "action_id": "zone.command", "diff": {**preview["diff"], "before": old_before},
        "blast_radius": preview["blast_radius"], "resource_etag": preview["resource_etag"],
        "rollback_recipe": preview["rollback_recipe"],
    })
    await sql(url, """
        UPDATE connector_changes SET preview_hash=:hash, before=before-'controls', after=after-'controls',
            rollback_from=(rollback_from->'state')-'controls', rollback_after=rollback_after-'controls'
        WHERE id=:id
    """, {"id": uuid.UUID(preview["proposal_id"]), "hash": old_hash if historical_hash else preview["preview_hash"]})
    if not settled or not historical_hash:
        with pytest.raises(bk.BackupError, match="connectorherstelbewijs"):
            await manual_dump()
        return
    record = await manual_dump()
    assert record["status"] == "ok"
    evidence = await proef.evidence(rollback_id)
    assert evidence["rollback_reason_code"] == "readback_incomplete" and evidence["rollback_preview"] is None
    assert len(proef.unit.commands) == 2


async def test_geannuleerde_opdracht_zonder_bewijs_blijft_verweesd(proef, manual_dump):
    preview = await proef.propose()
    response = await proef.post("apply", proef.request(preview=preview))
    assert response.status_code == 202
    url = get_settings().database_url
    await sql(url, "UPDATE commands SET status='cancelled' WHERE id=:id", {"id": uuid.UUID(response.json()["command_id"])})
    await sql(url, "DELETE FROM connector_changes WHERE id=:id", {"id": uuid.UUID(preview["proposal_id"])})
    with pytest.raises(bk.BackupError, match="connectorherstelbewijs"):
        await manual_dump()
    assert not proef.unit.commands
