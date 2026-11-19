"""Contract 1.3 §4.5, §5 en §8 met lokaal gegenereerde Ed25519-sleutels."""

import asyncio
import base64
import json
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from alembic import command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException

from shc_api import backup as bk
from shc_api import connector
from shc_api.cli import _libpq
from shc_api.contract_http import _http_error
from shc_api.hub_keys import parse_pasted_keyset
from shc_api.models import ConnectorHubPin, ConnectorNonce, IdempotencyKey, Setting

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


def test_geen_private_velden_in_een_geplakte_sleutel():
    jwt, policy = (jwk(Ed25519PrivateKey.generate(), name) for name in ("jwt", "policy"))
    policy["d"] = "ongeldig-testveld"
    with pytest.raises(ValueError):
        parse_pasted_keyset(pasted([jwt], policy))


def test_sleutelmateriaal_per_doel_gescheiden():
    key = Ed25519PrivateKey.generate()
    with pytest.raises(ValueError):
        parse_pasted_keyset(pasted([jwk(key, "jwt")], jwk(key, "policy")))


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


# De vormen volgen de Hub-vormcontrole voor ExecutionRequest.
@pytest.mark.parametrize("version", ["sha256:" + "a" * 64, "absent", "dataset:proef", "snapshot:proef", "geen", "eigenaar"])
def test_ondersteunde_versievormen(version):
    request = envelope(Ed25519PrivateKey.generate())
    request["proposal"]["expected_version"] = version
    connector._validate_envelope(request)


@pytest.mark.parametrize("version", ["", " ", "dataset:", "snapshot:met spatie", "sha256:te-kort"])
def test_ongeldige_versie_wordt_geweigerd(version):
    request = envelope(Ed25519PrivateKey.generate())
    request["proposal"]["expected_version"] = version
    with pytest.raises(ValueError):
        connector._validate_envelope(request)


@pytest.fixture
async def connector_db(monkeypatch):
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


async def connector_row_counts(db):
    counts = [
        await db.scalar(select(func.count()).select_from(model))
        for model in (ConnectorHubPin, ConnectorNonce, IdempotencyKey)
    ]
    return tuple(counts)


@pytest.mark.parametrize(("field", "value"), [("policy_keys", []), ("fingerprint", "ongeldig")])
async def test_onbruikbare_pin_geeft_eenduidige_status(connector_db, field, value):
    sessions, clock = connector_db
    key = Ed25519PrivateKey.generate()
    async with sessions() as db:
        assert await connector.pinned_status(db) is None
    original = await pin(sessions, key)
    clock[0] = NOW
    async with sessions.begin() as db:
        assert await connector.pinned_status(db) == original
        setattr(await db.get(ConnectorHubPin, 1), field, value)
    with pytest.raises(HTTPException) as verify_error:
        await apply(sessions, envelope(key), forbidden_operation)
    async with sessions() as db:
        assert await connector_row_counts(db) == (1, 0, 0)
        with pytest.raises(HTTPException) as status_error:
            await connector.pinned_status(db)
    for error in (verify_error, status_error):
        assert (error.value.status_code, error.value.detail["code"]) == (503, "connector_pins_unavailable")
        assert original.fingerprint.encode() not in _http_error(error.value).body


async def test_tijdelijke_pinstoring_geeft_retry_after(connector_db):
    sessions, _ = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    async with sessions.begin() as db:
        (await db.get(ConnectorHubPin, 1)).policy_keys = []
    with pytest.raises(HTTPException) as error:
        await apply(sessions, envelope(key), forbidden_operation)
    response = _http_error(error.value)
    assert response.status_code == 503
    retry_after = response.headers.get("retry-after", "")
    assert retry_after.isascii() and retry_after.isdecimal() and int(retry_after) > 0


@pytest.mark.parametrize("change", ["missing_signature", "wrong_signer", "unknown_field", "float"])
async def test_foutpad_toont_geen_verzoekinhoud(connector_db, caplog, change):
    sessions, clock = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    clock[0] = NOW
    marker = "alleen-voor-de-lokale-foutproef"
    request = envelope(key)
    del request["signature"]
    request["proposal"]["reason"] = marker
    signer = Ed25519PrivateKey.generate() if change == "wrong_signer" else key
    if change != "missing_signature":
        request["signature"] = b64(signer.sign(canonical(request)))
    if change == "unknown_field":
        request["unknown"] = marker
    elif change == "float":
        request["proposal"]["parameters"]["amount"] = 0.5

    with caplog.at_level("INFO"), pytest.raises(HTTPException) as error:
        await apply(sessions, request, forbidden_operation)
    response = _http_error(error.value)
    assert response.status_code == (403 if change == "wrong_signer" else 422)
    exposed = response.body.decode() + caplog.text
    assert marker not in exposed
    if "signature" in request:
        assert request["signature"] not in exposed
    async with sessions() as db:
        assert await connector_row_counts(db) == (1, 0, 0)


@pytest.mark.parametrize(("seconds", "accepted"), [(900, True), (901, False)])
async def test_verzoekgeldigheid_is_hooguit_vijftien_minuten(connector_db, seconds, accepted):
    sessions, clock = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    clock[0] = NOW
    request = envelope(key, issued=NOW, expires=NOW + timedelta(seconds=seconds))
    if accepted:
        operation = AsyncMock(return_value=(200, {}))
        assert await apply(sessions, request, operation) == (200, {}, False)
        operation.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as error:
            await apply(sessions, request, forbidden_operation)
        assert (error.value.status_code, error.value.detail["code"]) == (403, "verzoek_geldigheid_ongeldig")


async def test_databasestoring_sluit_voor_de_actie(connector_db, monkeypatch):
    sessions, _ = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    monkeypatch.setattr(connector, "lock_commissioning_shared", AsyncMock(side_effect=ConnectionError))
    with pytest.raises(ConnectionError):
        await apply(sessions, envelope(key), forbidden_operation)
    async with sessions() as db:
        assert await connector_row_counts(db) == (1, 0, 0)


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


async def test_rotatie_weigert_sleutelmateriaal_van_ander_doel(connector_db):
    sessions, clock = connector_db
    jwt, policy = (Ed25519PrivateKey.generate() for _ in range(2))
    original = await pin(sessions, policy, jwt_key=jwt)
    clock[0] = NOW
    item = rotation(policy, "policy", 1, "policy", add=[jwk(jwt, "policy-nieuw")])
    async with sessions() as db:
        with pytest.raises(HTTPException) as error:
            await connector.preview_rotation(db, [item])
        assert (error.value.status_code, error.value.detail["code"]) == (422, "sleutelrotatie_ongeldig")
        assert await connector.pinned_status(db) == original


async def test_herplak_van_oudere_volgorde_wordt_geweigerd(connector_db):
    sessions, _ = connector_db
    jwt, policy = (jwk(Ed25519PrivateKey.generate(), name) for name in ("jwt", "policy"))
    source = json.loads(pasted([jwt], policy))
    source["rotation_sequence"] = 4
    fingerprint = parse_pasted_keyset(json.dumps(source)).fingerprint
    async with sessions.begin() as db:
        original = await connector.confirm_paste(db, json.dumps(source), fingerprint, replacing=None)
    source["rotation_sequence"] -= 1
    async with sessions.begin() as db:
        with pytest.raises(HTTPException) as error:
            await connector.confirm_paste(db, json.dumps(source), fingerprint, replacing=original)
        assert (error.value.status_code, error.value.detail["code"]) == (409, "rotatievolgorde_ouder")
        assert await connector.pinned_status(db) == original


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


@pytest.fixture
async def gevulde_koppeling(connector_db):
    sessions, clock = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    clock[0] = NOW
    await apply(sessions, envelope(key), AsyncMock(return_value=(200, {})))
    ordinary = {"v": {"naam": "Gewone proefinstelling"}}
    async with sessions.begin() as db:
        db.add(Setting(key="connector_proef", value=ordinary))
        assert await connector_row_counts(db) == (1, 1, 1)
    return sessions, ordinary


async def test_shcbak_slaat_gevulde_koppeltabellen_over(gevulde_koppeling, tmp_path):
    sessions, ordinary = gevulde_koppeling
    path = tmp_path / "export.shcbak"
    async with sessions() as db, db.bind.connect() as conn:
        await bk.write_backup(conn, path, PW, include_secrets=True)
        url = db.bind.url.render_as_string(hide_password=False)
    manifest = bk.verify_file(path, PW).manifest
    for table in ("connector_hub_pins", "connector_nonces", "idempotency_keys"):
        assert table not in manifest["tables"]
        assert all(not name.startswith(f"tables/{table}/") for name in manifest["files"])

    async with sessions.begin() as db:
        (await db.get(Setting, "connector_proef")).value = {"v": "na export gewijzigd"}
    await bk.restore_file(path, PW, url=url)
    async with sessions() as db:
        assert await connector_row_counts(db) == (0, 0, 0)
        assert (await db.get(Setting, "connector_proef")).value == ordinary


async def test_shcdump_slaat_gevulde_koppeltabellen_over(gevulde_koppeling, tmp_path):
    sessions, ordinary = gevulde_koppeling
    async with sessions() as db:
        url = db.bind.url.render_as_string(hide_password=False)
    record = await bk.run_db_dump(PW, auto=True, url=url, backup_dir=tmp_path)
    assert record["status"] == "ok"
    archive = tmp_path / "proef.pgdump"
    try:
        bk.decrypt_dump(tmp_path / record["filename"], PW, archive)
        async with temp_db() as target:
            dsn, _ = _libpq(target)
            await asyncio.to_thread(
                subprocess.run,  # noqa: S603 - vast programma en lokale proefdatabase
                ["pg_restore", "--single-transaction", "--clean", "--if-exists", "--dbname", dsn, str(archive)],
                check=True,
                capture_output=True,
            )
            engine = create_async_engine(target)
            try:
                async with async_sessionmaker(engine)() as db:
                    assert await connector_row_counts(db) == (0, 0, 0)
                    assert (await db.get(Setting, "connector_proef")).value == ordinary
            finally:
                await engine.dispose()
    finally:
        archive.unlink(missing_ok=True)


async def test_idempotentiewacht_vervalt_zonder_tweede_actie(connector_db):
    sessions, clock = connector_db
    key = Ed25519PrivateKey.generate()
    await pin(sessions, key)
    clock[0] = NOW
    request, idem = envelope(key), str(uuid.uuid4())
    handling, finish = asyncio.Event(), asyncio.Event()

    async def operation(db, verified):
        handling.set()
        await finish.wait()
        return 202, {"status": "ingepland"}

    first = asyncio.create_task(apply(sessions, request, operation, idem=idem))
    try:
        await asyncio.wait_for(handling.wait(), timeout=5)
        with pytest.raises(HTTPException) as refused:
            async with asyncio.timeout(5):
                await apply(sessions, request, forbidden_operation, idem=idem)
        assert (refused.value.status_code, refused.value.detail["code"]) == (409, "idempotency_in_progress")
    finally:
        finish.set()
        await first
    assert await apply(sessions, request, forbidden_operation, idem=idem) == (202, {"status": "ingepland"}, True)
