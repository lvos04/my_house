"""Contract 1.3 §4.5, §5 en §8 met lokaal gegenereerde Ed25519-sleutels."""

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException

from shc_api import backup as bk

from .test_inbedrijfstelling_migratie import sql
from .test_ws_backup_format import PW, make_v1, temp_db

NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)
X_JWT = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"
X_POLICY = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg"
X_EXTRA = "Kay64UG8yvCyLhqU000LxzYeUm0L_hLIl5S8kyKWbdc"


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def jwk(key, kid):
    x = b64(key.public_key().public_bytes_raw()) if isinstance(key, Ed25519PrivateKey) else key
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": kid, "use": "sig", "alg": "EdDSA"}


def pasted(jwt_keys, policy_keys):
    return json.dumps({"jwks": {"keys": jwt_keys}, "policy_public_key": policy_keys})


@pytest.mark.parametrize(
    ("jwt_keys", "policy_keys", "expected"),
    [
        ([X_JWT], X_POLICY, "MEGG-8GFG-2E4Y-K2RQ"),
        ([X_EXTRA, X_JWT], X_POLICY, "CYRC-JS8C-14AN-JR17"),
        ([X_JWT], [X_POLICY, X_EXTRA], "HN69-2PHK-XV2D-X85W"),
        ([X_JWT], [X_EXTRA, X_POLICY], "HN69-2PHK-XV2D-X85W"),
    ],
)
def test_contractvectoren(jwt_keys, policy_keys, expected):
    from shc_api.hub_keys import parse_pasted_keyset

    jwt = [jwk(x, str(i)) for i, x in enumerate(jwt_keys)]
    policy = (
        jwk(policy_keys, "policy")
        if isinstance(policy_keys, str)
        else [jwk(x, str(i)) for i, x in enumerate(policy_keys)]
    )
    assert parse_pasted_keyset(pasted(jwt, policy)).fingerprint == expected


def test_crockford_aliases_en_geen_unicode_tekens():
    from shc_api.hub_keys import fingerprints_match

    assert fingerprints_match("01AZ-1110-0101-0000", "oLaZ illO oioL oooo")
    assert not fingerprints_match("01AZ-1110-0101-0000", "Ｏ1AZ-1110-0101-0000")


def iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def envelope(key, *, issued=None, expires=None, nonce=None, policy_hash=None):
    body = {
        "proposal": {
            "idempotency_key": str(uuid.uuid4()),
            "action_id": "light.group.set",
            "target": {"group_id": "proefgroep"},
            "parameters": {"name": "Proeflamp é", "on": True},
            "expected_version": "sha256:" + "a" * 64,
            "actor": {"type": "service", "id": "hub"},
            "risk_claim": "R0",
        },
        "approval": None,
        "policy_decision": {
            "risk_class": "R1",
            "rule_ids": ["test"],
            "policy_hash": policy_hash or "sha256:" + "b" * 64,
        },
        "issued_at": iso(issued or NOW - timedelta(seconds=1)),
        "expires_at": iso(expires or NOW + timedelta(minutes=1)),
        "nonce": str(nonce or uuid.uuid4()),
    }
    return {**body, "signature": b64(key.sign(canonical(body)))}


@pytest.fixture
async def connector_db(monkeypatch):
    from shc_api import connector

    async with temp_db() as url:
        engine = create_async_engine(url)
        clock = [NOW - timedelta(minutes=10)]
        monkeypatch.setattr(connector, "utcnow", lambda: clock[0])
        try:
            yield async_sessionmaker(engine, expire_on_commit=False), clock
        finally:
            await engine.dispose()


async def pin(sessions, key, *, jwt_key=None, fingerprint=None):
    from shc_api.connector import confirm_paste
    from shc_api.hub_keys import parse_pasted_keyset

    source = pasted([jwk(jwt_key or Ed25519PrivateKey.generate(), "jwt")], jwk(key, "policy"))
    expected = parse_pasted_keyset(source).fingerprint
    async with sessions.begin() as db:
        return await confirm_paste(db, source, fingerprint or expected.lower().replace("-", " "), replacing=None)


async def apply(sessions, request, handler, *, idem="random"):
    from shc_api.connector import process_request

    async with sessions.begin() as db:
        return await process_request(
            db,
            scope="POST /connector/v1/apply",
            idempotency_key=str(uuid.uuid4()) if idem == "random" else idem,
            request=request,
            operation=handler,
        )


async def forbidden_operation(*_):
    pytest.fail("Een geweigerd verzoek bereikte de actie.")


async def test_zonder_pin_501_en_verkeerde_vingerafdruk(connector_db):
    sessions, _ = connector_db
    key = Ed25519PrivateKey.generate()
    with pytest.raises(HTTPException) as invalid:
        await pin(sessions, key, fingerprint="0000-0000-0000-0000")
    assert invalid.value.detail["code"] == "vingerafdruk_verschilt"
    with pytest.raises(HTTPException) as unavailable:
        await apply(sessions, envelope(key), forbidden_operation)
    assert (unavailable.value.status_code, unavailable.value.detail["code"]) == (501, "capability_unavailable")


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("unknown_key", "handtekening_ongeldig"),
        ("jwt_key", "handtekening_ongeldig"),
        ("expired", "verzoek_verlopen"),
        ("future", "verzoek_nog_niet_geldig"),
        ("tampered", "handtekening_ongeldig"),
        ("hash_format", "execution_request_ongeldig"),
        ("hash_tampered", "handtekening_ongeldig"),
    ],
)
async def test_ongeldig_verzoek_bereikt_de_actie_niet(connector_db, case, reason):
    sessions, clock = connector_db
    key, jwt_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    await pin(sessions, key, jwt_key=jwt_key)
    clock[0] = NOW
    request = envelope(key)
    if case == "unknown_key":
        request = envelope(Ed25519PrivateKey.generate())
    elif case == "jwt_key":
        request = envelope(jwt_key)
    elif case == "expired":
        request = envelope(key, expires=NOW)
    elif case == "future":
        request = envelope(key, issued=NOW + timedelta(seconds=1))
    elif case == "tampered":
        request["proposal"]["parameters"]["on"] = False
    elif case == "hash_format":
        request = envelope(key, policy_hash="afwijkende vorm")
    elif case == "hash_tampered":
        request["policy_decision"]["policy_hash"] = "sha256:" + "d" * 64
    with pytest.raises(HTTPException) as error:
        await apply(sessions, request, forbidden_operation)
    assert error.value.detail["code"] == reason


@pytest.mark.parametrize(
    ("header", "reason"),
    [
        (None, "idempotency_key_required"),
        ("ongeldig", "idempotency_key_invalid"),
    ],
)
async def test_idempotentiesleutel_vereist(connector_db, header, reason):
    sessions, clock = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    clock[0] = NOW
    with pytest.raises(HTTPException) as refused:
        await apply(sessions, envelope(key), forbidden_operation, idem=header)
    assert (refused.value.status_code, refused.value.detail["code"]) == (400, reason)


@pytest.mark.parametrize("same_idempotency_key", [False, True])
async def test_gelijktijdige_verzoeken(connector_db, same_idempotency_key):
    sessions, clock = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    clock[0] = NOW
    first_in_action = asyncio.Event()
    second_pid = asyncio.get_running_loop().create_future()
    finish = asyncio.Event()
    calls = 0

    async def operation(db, verified):
        nonlocal calls
        calls += 1
        assert "risk_claim" not in verified.proposal
        assert verified.risk_class == "R1"
        assert verified.policy_hash == "sha256:" + "b" * 64
        await db.execute(text("INSERT INTO settings (key,value) VALUES ('connector_proef','{}')"))
        first_in_action.set()
        await finish.wait()
        return 202, {"status": "ingepland"}

    request = envelope(key)
    idem = str(uuid.uuid4())

    async def second():
        from shc_api.connector import process_request

        async with sessions() as db:
            async with db.begin():
                second_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
                return await process_request(
                    db,
                    scope="POST /connector/v1/apply",
                    idempotency_key=idem if same_idempotency_key else str(uuid.uuid4()),
                    request=request,
                    operation=operation,
                )

    first = asyncio.create_task(apply(sessions, request, operation, idem=idem))
    try:
        await asyncio.wait_for(first_in_action.wait(), timeout=5)
        other = asyncio.create_task(second())
        try:
            async with asyncio.timeout(10):
                pid = await second_pid
                async with sessions() as observer:
                    while not await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid}):
                        await asyncio.sleep(0.01)
        finally:
            finish.set()
            result, duplicate = await asyncio.gather(first, other, return_exceptions=True)
    finally:
        finish.set()
        await asyncio.gather(first, return_exceptions=True)

    assert result == (202, {"status": "ingepland"}, False)
    if same_idempotency_key:
        assert duplicate == (202, {"status": "ingepland"}, True)
    else:
        assert isinstance(duplicate, HTTPException) and duplicate.detail["code"] == "nonce_al_gebruikt"
    assert calls == 1
    with pytest.raises(HTTPException) as error:
        await apply(sessions, envelope(key), forbidden_operation, idem=idem)
    assert (error.value.status_code, error.value.detail["code"]) == (422, "idempotency_key_reused")


async def test_rollback_maakt_nonce_en_idempotentiesleutel_opnieuw_beschikbaar(connector_db):
    sessions, clock = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    clock[0] = NOW
    request, idem = envelope(key), str(uuid.uuid4())

    async def fail(db, _):
        await db.execute(text("INSERT INTO settings (key,value) VALUES ('connector_proef','{}')"))
        raise RuntimeError("Proef voor rollback")

    async def succeed(db, _):
        await db.execute(text("INSERT INTO settings (key,value) VALUES ('connector_proef','{}')"))
        return 202, {"status": "ingepland"}

    with pytest.raises(RuntimeError, match="rollback"):
        await apply(sessions, request, fail, idem=idem)
    assert await apply(sessions, request, succeed, idem=idem) == (202, {"status": "ingepland"}, False)
    clock[0] += timedelta(minutes=2)
    assert await apply(sessions, request, forbidden_operation, idem=idem) == (202, {"status": "ingepland"}, True)


def rotation(key, kid, seq, purpose, *, add=(), retire=()):
    header = {"alg": "EdDSA", "typ": "hub-key-rotation+jws", "kid": kid}
    body = {
        "type": "hub_key_rotation",
        "iss": "hub",
        "sequence": seq,
        "issued_at": iso(NOW),
        "purpose": purpose,
        "add": list(add),
        "retire": list(retire),
    }
    message = f"{b64(canonical(header))}.{b64(canonical(body))}"
    return {"sequence": seq, "statement": f"{message}.{b64(key.sign(message.encode('ascii')))}"}


async def test_rotatie_pin_verandert_pas_na_bevestiging(connector_db):
    from shc_api.connector import confirm_rotation, pinned_status, preview_rotation

    sessions, clock = connector_db
    jwt_key, jwt_next, policy, policy_next, unknown = (Ed25519PrivateKey.generate() for _ in range(5))
    old = await pin(sessions, policy, jwt_key=jwt_key)
    clock[0] = NOW
    jwt_add = rotation(jwt_key, "jwt", 1, "jwt", add=[jwk(jwt_next, "jwt-nieuw")])
    policy_add = rotation(policy, "policy", 2, "policy", add=[jwk(policy_next, "nieuw")])
    jwks_gelijk_kid = rotation(unknown, "policy", 2, "policy", add=[jwk(policy_next, "nieuw")])
    async with sessions.begin() as db:
        for items in [
            [policy_add],
            [jwt_add, jwks_gelijk_kid],
            [jwt_add, rotation(jwt_key, "jwt", 2, "policy", add=[jwk(policy_next, "nieuw")])],
            [jwt_add, rotation(policy, "policy", 2, "policy", retire=["policy"])],
        ]:
            with pytest.raises(HTTPException) as refused:
                await preview_rotation(db, items)
            assert refused.value.detail["code"] == "sleutelrotatie_ongeldig"
        assert await pinned_status(db) == old
        preview = await preview_rotation(db, [jwt_add, policy_add])
        assert preview.previous == old and preview.sequence == 2
        assert await pinned_status(db) == old
    async with sessions.begin() as db:
        with pytest.raises(HTTPException) as refused:
            await confirm_rotation(db, [jwt_add, policy_add], old.fingerprint, replacing=old)
        assert refused.value.detail["code"] == "vingerafdruk_verschilt"
        assert await pinned_status(db) == old
        accepted = await confirm_rotation(db, [jwt_add, policy_add], preview.fingerprint, replacing=old)
    async with sessions.begin() as db:
        assert await pinned_status(db) == accepted
        with pytest.raises(HTTPException) as refused:
            await confirm_rotation(db, [jwt_add, policy_add], preview.fingerprint, replacing=old)
        assert refused.value.detail["code"] == "sleutelset_gewijzigd"
        retirement = rotation(policy_next, "nieuw", 3, "policy", retire=["policy"])
        final = await preview_rotation(db, [retirement])
        await confirm_rotation(db, [retirement], final.fingerprint, replacing=accepted)

    with pytest.raises(HTTPException) as refused:
        await apply(sessions, envelope(policy), forbidden_operation)
    assert refused.value.detail["code"] == "handtekening_ongeldig"

    async def accepted_operation(*_):
        return 200, {"status": "ingelezen"}

    assert await apply(sessions, envelope(policy_next), accepted_operation) == (200, {"status": "ingelezen"}, False)


async def test_migratie_op_neer_op_bewaart_bestaande_rijen():
    async with temp_db("0005") as url:
        await sql(
            url,
            """
            INSERT INTO homes (id,name,timezone,currency,active_from)
            VALUES ('10000000-0000-0000-0000-000000000001','Proefwoning','UTC','EUR',now())
        """,
        )
        await sql(
            url,
            """
            INSERT INTO users (id,username,password_hash,role,disabled,created_at)
            VALUES ('10000000-0000-0000-0000-000000000002','Proefbeheerder','n.v.t.','admin',true,now())
        """,
        )
        before = await sql(url, "SELECT row_to_json(h)::text FROM homes h")
        users = await sql(url, "SELECT row_to_json(u)::text FROM users u")
        settings = await sql(url, "SELECT key, value::text FROM settings ORDER BY key")
        cfg = bk.alembic_config(url)
        for migrate, target, present in [
            (command.upgrade, "head", True),
            (command.downgrade, "0005", False),
            (command.upgrade, "head", True),
        ]:
            await asyncio.to_thread(migrate, cfg, target)
            assert await sql(url, "SELECT row_to_json(h)::text FROM homes h") == before
            assert await sql(url, "SELECT row_to_json(u)::text FROM users u") == users
            assert await sql(url, "SELECT key, value::text FROM settings ORDER BY key") == settings
            tables = await sql(
                url,
                """
                SELECT table_name FROM information_schema.tables WHERE table_schema='public'
                AND table_name IN ('connector_nonces','idempotency_keys','connector_hub_pins') ORDER BY table_name
            """,
            )
            assert tables == (
                [("connector_hub_pins",), ("connector_nonces",), ("idempotency_keys",)] if present else []
            )


async def test_herstel_neemt_geen_vertrouwen_uit_archief_over(connector_db, tmp_path):
    from shc_api.connector import pinned_status

    sessions, clock = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    old_request = envelope(key)
    async with sessions() as db, db.bind.connect() as conn:
        manifest = await bk.write_backup(conn, tmp_path / "export.shcbak", PW, include_secrets=True)
    assert {"connector_hub_pins", "connector_nonces", "idempotency_keys"}.isdisjoint(manifest["tables"])

    path = tmp_path / "proef.shcbak"
    # Als een archief toch pinrijen aanbiedt, mogen die nooit opnieuw vertrouwen geven.
    path.write_bytes(make_v1({"connector_hub_pins": [{"id": 1}]}, bk.head_revision()))
    async with sessions() as db:
        url = db.bind.url.render_as_string(hide_password=False)
        report = await bk.restore_file(path, PW, url=url)
        assert report.dropped["tables"]["connector_hub_pins"] == 1
        assert await pinned_status(db) is None
    clock[0] = NOW
    await pin(sessions, key)
    clock[0] += timedelta(seconds=2)
    with pytest.raises(HTTPException) as refused:
        await apply(sessions, old_request, forbidden_operation)
    assert refused.value.detail["code"] == "verzoek_voor_koppeling"
