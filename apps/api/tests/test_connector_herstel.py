"""Regressieproeven voor het bewaakte herstelpad met de Gree-simulator."""

import asyncio
import subprocess
import uuid

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api import cli
from shc_api.cli import _libpq
from shc_api.config import get_settings
from shc_api.connector_changes import digest
from shc_api.db import sessionmaker
from shc_api.models import ConnectorHubPin, utcnow
from shc_api.worker import commands

from .test_connector_routes import proef as proef
from .test_inbedrijfstelling_migratie import sql
from .test_ws_backup_format import PW, temp_db


async def _apply(proef):
    preview = await proef.propose()
    answer = await proef.post("apply", proef.request(preview=preview))
    assert answer.status_code == 202, answer.json()
    await proef.work()
    return preview, answer.json()["command_id"]


def _rollback_request(proef, preview, evidence):
    assert evidence["rollback_preview"] is not None
    return proef.request(preview=preview, rollback=evidence["rollback_preview"])


async def _queue_rollback(proef, preview):
    evidence = await proef.evidence(preview["proposal_id"])
    answer = await proef.post("rollback", _rollback_request(proef, preview, evidence))
    assert answer.status_code == 202, answer.json()
    return answer.json()["command_id"]


@pytest.mark.parametrize("error", ["mislukt", "onvolledig"])
async def test_ongeldig_statusantwoord_levert_geen_vorige_stand(proef, monkeypatch, error):
    original = proef.unit._reply
    proef.unit.state["SetTem"] = 24

    def reply(inner, *args, **kwargs):
        if inner["t"] == "dat":
            inner["dat"][inner["cols"].index("SetTem")] = 20
            if error == "mislukt":
                inner["r"] = 500
            else:
                inner["dat"].pop()
        return original(inner, *args, **kwargs)

    monkeypatch.setattr(proef.unit, "_reply", reply)
    preview = await proef.propose()
    assert preview["status"] == "failed" and preview["reason_code"] == "readback_unavailable"
    assert preview["before"] is None and preview["rollback_recipe"] is None
    answer = await proef.post("apply", proef.request(preview=preview))
    assert answer.status_code == 409
    assert await proef.count() == 0 and not proef.unit.commands and proef.unit.state["SetTem"] == 24


@pytest.mark.parametrize("field,new_value", [("WdSpd", 3), ("SwUpDn", 2), ("Quiet", 1)])
async def test_nevenwijziging_is_zichtbaar_en_blijft_open(proef, monkeypatch, field, new_value):
    original = proef.unit.datagram_received
    before = proef.unit.state[field]

    def receive(message, addr):
        original(message, addr)
        if proef.unit.commands:
            proef.unit.state[field] = new_value

    monkeypatch.setattr(proef.unit, "datagram_received", receive)
    preview, _ = await _apply(proef)
    result = await proef.evidence(preview["proposal_id"])
    assert result["status"] == "failed" and result["reason_code"] == "readback_side_effect"
    assert result["before"]["controls"][field] == before
    assert result["after"]["controls"][field] == new_value
    assert result["recovery_required"] and result["rollback_status"] != "confirmed"
    assert result["rollback_preview"] is None
    assert len(proef.unit.commands) == 1


@pytest.mark.parametrize("tussenopdracht", [False, True])
async def test_herstel_opnieuw_na_weigering_voor_verzending(proef, monkeypatch, tussenopdracht):
    preview, _ = await _apply(proef)
    first = await _queue_rollback(proef, preview)
    with monkeypatch.context() as patch:
        original = proef.unit._reply

        def reply(inner, *args, **kwargs):
            if inner["t"] != "dat":
                return original(inner, *args, **kwargs)

        patch.setattr(proef.unit, "_reply", reply)
        await proef.work()
    failed = await proef.evidence(first)
    assert failed["rollback_status"] == "failed" and failed["recovery_required"]
    assert failed["rollback_reason_code"] == "readback_before_send_unavailable"
    assert len(proef.unit.commands) == 1
    if tussenopdracht:
        key = proef.key_id
        proef.key_id = str(uuid.uuid4())
        await _apply(proef)
        proef.key_id = key
        denied = await proef.post("rollback", _rollback_request(proef, preview, failed))
        assert denied.status_code == 409 and denied.json()["code"] == "precondition_failed"
        assert await proef.count() == 3 and len(proef.unit.commands) == 2
        return
    second = await _queue_rollback(proef, preview)
    assert second != first
    await proef.work()
    done = await proef.evidence(second)
    assert done["rollback_status"] == "confirmed" and done["rollback_after"] == done["before"]
    assert (await proef.evidence(first))["previous_rollback_command_ids"] == [first]
    assert not done["recovery_required"] and len(proef.unit.commands) == 2
    assert proef.unit.state["SetTem"] == 20


async def test_downgrade_bewaart_open_rollback_voor_handmatige_controle(proef):
    preview, _ = await _apply(proef)
    rollback_id = await _queue_rollback(proef, preview)
    url = get_settings().database_url
    await asyncio.to_thread(command.downgrade, bk.alembic_config(url), "0006")
    try:
        assert await sql(url, "SELECT status, attempts, detail FROM commands WHERE id=:id",
                         {"id": uuid.UUID(rollback_id)}) == [("cancelled", 0, "versiewissel")]
        assert await sql(url, "SELECT title FROM alerts WHERE key=:key",
                         {"key": "connector:rollback:" + rollback_id}) == [("Handmatige controle nodig",)]
        assert await sql(url, "SELECT value->'v'->>'preview_hash' FROM settings WHERE key=:k",
                         {"k": "connector_history:" + preview["proposal_id"]}) == [(preview["preview_hash"],)]
    finally:
        await asyncio.to_thread(command.upgrade, bk.alembic_config(url), "head")
    assert (await proef.evidence(rollback_id))["recovery_required"]
    assert proef.unit.state["SetTem"] == 22 and len(proef.unit.commands) == 1


@pytest.mark.parametrize("archief", ["export", "export_zonder_bewijs", "dump", "dump_zonder_bewijs"])
async def test_back_up_bewaart_open_herstel_zonder_uitvoerrechten(proef, tmp_path, monkeypatch, archief):
    preview, command_id = await _apply(proef)
    rollback_id = await _queue_rollback(proef, preview)
    url = get_settings().database_url
    async with temp_db() as dest:
        if archief.startswith("dump"):
            path = tmp_path / "herstel.shcdump"
            if archief == "dump_zonder_bewijs":
                monkeypatch.setattr(bk, "CONNECTOR_LOCAL_TABLES", bk.CONNECTOR_LOCAL_TABLES | {"connector_changes"})
            await asyncio.to_thread(bk._pg_dump_sync, url, path, PW, utcnow().isoformat())
            plain = tmp_path / "herstel.pgdump"
            if archief == "dump_zonder_bewijs":
                with pytest.raises(bk.BackupError, match="het connectorherstelbewijs ontbreekt"):
                    await asyncio.to_thread(bk.decrypt_dump, path, PW, plain)
                assert not plain.exists()
                return
            await asyncio.to_thread(bk.decrypt_dump, path, PW, plain)
            dsn, _ = _libpq(dest)
            await asyncio.to_thread(
                subprocess.run,  # noqa: S603 - vast programma en lokale proefdatabase
                ["pg_restore", "--single-transaction", "--clean", "--if-exists", "--dbname", dsn, str(plain)],
                check=True,
                capture_output=True,
            )
        else:
            engine = create_async_engine(url, poolclass=NullPool)
            path = tmp_path / "herstel.shcbak"
            if archief == "export_zonder_bewijs":
                monkeypatch.setattr(bk, "SKIP_TABLES", bk.SKIP_TABLES | {"connector_changes"})
            try:
                async with engine.connect() as conn:
                    await bk.write_backup(conn, path, PW, include_secrets=False)
            finally:
                await engine.dispose()
            verified = bk.verify_file(path, PW)
            if archief == "export_zonder_bewijs":
                with pytest.raises(bk.BackupError, match="ontbreekt het herstelbewijs"):
                    await bk.restore_file(path, PW, url=dest, verified=verified)
                assert await sql(dest, "SELECT count(*) FROM connector_changes") == [(0,)]
                return
            await bk.restore_file(path, PW, url=dest, verified=verified)
            assert (await proef.worker._claim_next({proef.home_id}))[0] == uuid.UUID(rollback_id)
            with pytest.raises(bk.BackupError, match="bestaande open apparaatherstel"):
                await bk.restore_file(path, PW, url=url, verified=verified)
            assert await sql(url, "SELECT status FROM commands WHERE id = :id", {"id": uuid.UUID(rollback_id)}) == [
                ("sending",),
            ]
        rows = await sql(dest, """
            SELECT before, command_id, rollback_command_id, rollback_status, execution_expires_at, rollback_expires_at
            FROM connector_changes WHERE id = :id
        """, {"id": uuid.UUID(preview["proposal_id"])})
        assert len(rows) == 1
        assert rows[0][:4] == (preview["before"], uuid.UUID(command_id), uuid.UUID(rollback_id), "queued")
        if archief == "export":
            assert rows[0][4:] == (None, None)
        assert await sql(dest, "SELECT status FROM commands WHERE id = :id", {"id": uuid.UUID(rollback_id)}) == [
            ("queued" if archief == "dump" else "cancelled",),
        ]
        assert await sql(dest, "SELECT count(*) FROM connector_hub_pins") == [(0,)]
        if archief == "dump":
            engine = create_async_engine(dest, poolclass=NullPool)
            restored_sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessionmaker()() as source, restored_sessions.begin() as db:
                    pin = await source.get(ConnectorHubPin, 1)
                    db.add(ConnectorHubPin(
                        id=1, jwt_keys=pin.jwt_keys, policy_keys=pin.policy_keys, fingerprint=pin.fingerprint,
                        # Een bronklok die voorloopt mag oud opdrachtbewijs geen uitvoerrechten geven.
                        rotation_sequence=pin.rotation_sequence, accept_from=pin.accept_from,
                    ))
                monkeypatch.setattr(commands, "sessionmaker", lambda: restored_sessions)
                await cli.check_connector_transition(bk.head_revision(), dest)
                await proef.work()
            finally:
                await engine.dispose()
            assert await sql(dest, "SELECT status, attempts FROM commands WHERE id = :id", {"id": uuid.UUID(rollback_id)}) == [
                ("cancelled", 0),
            ]
    assert proef.unit.state["SetTem"] == 22 and len(proef.unit.commands) == 1


async def test_goedkeuring_is_gebonden_aan_verzoek_richting_en_wijziging(proef):
    preview = await proef.propose()
    wrong_id = proef.request(preview=preview)
    wrong_id["approval"]["request_id"] = str(uuid.uuid4())
    denied = await proef.post("apply", proef.sign(wrong_id))
    assert denied.status_code == 409 and denied.json()["code"] == "approval_mismatch"
    assert await proef.count() == 0
    accepted = await proef.post("apply", proef.request(preview=preview))
    assert accepted.status_code == 202
    await proef.work()
    forward = await proef.post("rollback", proef.request(preview=preview))
    assert forward.status_code == 409 and forward.json()["code"] == "approval_mismatch"
    evidence = await proef.evidence(preview["proposal_id"])
    wrong_params = _rollback_request(proef, preview, evidence)
    wrong_params["approval"]["action_scope"]["parameters_sha256"] = digest({"target_c": 22})
    denied = await proef.post("rollback", proef.sign(wrong_params))
    assert denied.status_code == 409 and denied.json()["code"] == "approval_mismatch"
    wrong_rollback_id = _rollback_request(proef, preview, evidence)
    wrong_rollback_id["approval"]["request_id"] = str(uuid.uuid4())
    denied = await proef.post("rollback", proef.sign(wrong_rollback_id))
    assert denied.status_code == 409 and denied.json()["code"] == "approval_mismatch"
    assert await proef.count() == 1 and len(proef.unit.commands) == 1
    await _queue_rollback(proef, preview)
    await proef.work()
    assert proef.unit.state["SetTem"] == 20 and len(proef.unit.commands) == 2


async def test_verloren_ontvangstbevestiging_herhaalt_schrijfactie_niet(proef, monkeypatch):
    original = proef.unit._reply
    dropped = False

    def reply(inner, *args, **kwargs):
        nonlocal dropped
        if inner["t"] == "res" and not dropped:
            dropped = True
            return None
        return original(inner, *args, **kwargs)

    monkeypatch.setattr(proef.unit, "_reply", reply)
    preview, _ = await _apply(proef)
    proof = await proef.evidence(preview["proposal_id"])
    assert dropped and proof["status"] == "confirmed"
    assert proef.unit.state["SetTem"] == 22 and len(proef.unit.commands) == 1
