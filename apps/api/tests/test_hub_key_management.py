import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import delete, func, select
from starlette.exceptions import HTTPException

from shc_api import connector
from shc_api.db import sessionmaker
from shc_api.hub_keys import parse_pasted_keyset
from shc_api.models import ConnectorHubPin

from .conftest import ADMIN_PW, Session
from .test_connector_verificatie import apply, b64, canonical, envelope, forbidden_operation, iso, jwk, pasted, rotation
from .test_ws_security_auth import PW, _user

PATH = "/system/hub-keys"


@pytest.fixture
async def pins(admin):
    async with sessionmaker().begin() as db:
        await db.execute(delete(ConnectorHubPin))
    yield admin
    async with sessionmaker().begin() as db:
        await db.execute(delete(ConnectorHubPin))


def keyset():
    policy = Ed25519PrivateKey.generate()
    source = pasted([jwk(Ed25519PrivateKey.generate(), "jwt")], jwk(policy, "policy"))
    return source, parse_pasted_keyset(source).fingerprint, policy


async def put(session, etag, data, *, key=None):
    headers = {**session.h(), "If-Match": etag, "Idempotency-Key": key or str(uuid.uuid4())}
    return await session.c.put("/api/v1" + PATH, headers=headers, json=data)


async def preview(session, etag, data):
    return await session.c.post(
        "/api/v1" + PATH + "/verify", headers={**session.h(), "If-Match": etag}, json=data
    )


async def install(session, source, fingerprint):
    empty = await session.get(PATH)
    assert empty.status_code == 200 and empty.json() == {"state": "empty"}
    data = {"mode": "paste", "source": source}
    result = await preview(session, empty.headers["etag"], data)
    assert result.status_code == 200
    assert result.json() == {"fingerprint": fingerprint, "sequence": "0"}
    assert (await session.get(PATH)).json() == {"state": "empty"}

    wrong = await put(session, empty.headers["etag"], {**data, "fingerprint": "0000"})
    assert (wrong.status_code, wrong.json()["code"]) == (422, "vingerafdruk_verschilt")
    assert (await session.get(PATH)).json() == {"state": "empty"}

    typed = fingerprint.lower().replace("-", " ").replace("0", "o").replace("1", "l")
    confirmation = {**data, "fingerprint": typed}
    key = str(uuid.uuid4())
    saved = await put(session, empty.headers["etag"], confirmation, key=key)
    assert saved.status_code == 200
    assert saved.json() == {"state": "pinned", "fingerprint": fingerprint, "sequence": "0"}
    replay = await put(session, empty.headers["etag"], confirmation, key=key)
    assert replay.json() == saved.json()
    assert replay.headers["etag"] == saved.headers["etag"]
    assert replay.headers["idempotent-replay"] == "true"
    reused = await put(session, empty.headers["etag"], {**confirmation, "fingerprint": "0000"}, key=key)
    assert (reused.status_code, reused.json()["code"]) == (422, "idempotency_key_reused")
    return saved.headers["etag"]


async def test_beheerder_pint_pas_na_vergelijking_en_met_csrf(pins):
    source, fingerprint, _ = keyset()
    etag = await install(pins, source, fingerprint)
    status = await pins.get(PATH)
    assert status.headers["etag"] == etag
    data = {"mode": "paste", "source": source, "fingerprint": fingerprint}
    no_csrf = await pins.c.put("/api/v1" + PATH, headers={"If-Match": etag}, json=data)
    assert no_csrf.status_code == 403
    no_key = await pins.c.put("/api/v1" + PATH, headers={**pins.h(), "If-Match": etag}, json=data)
    assert (no_key.status_code, no_key.json()["code"]) == (400, "idempotency_key_required")
    assert (await pins.get(PATH)).headers["etag"] == etag


async def test_kijker_en_anonieme_gebruiker_kunnen_geen_sleutelset_beheren(pins, app):
    source, fingerprint, _ = keyset()
    await _user(pins, "hub-kijker")
    etag = (await pins.get(PATH)).headers["etag"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as other:
        path = "/api/v1" + PATH
        assert (await other.get(path)).status_code == 401
        login = await other.post("/api/v1/auth/login", json={"username": "hub-kijker", "password": PW})
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrf_token"], "If-Match": etag, "Idempotency-Key": str(uuid.uuid4())}
        data = {"mode": "paste", "source": source}
        assert (await other.get(path)).status_code == 403
        assert (await other.post(path + "/verify", headers=headers, json=data)).status_code == 403
        assert (await other.put(path, headers=headers, json={**data, "fingerprint": fingerprint})).status_code == 403
    assert (await pins.get(PATH)).json() == {"state": "empty"}


async def test_ondertekende_rotatie_blijft_een_preview_tot_bevestiging(pins):
    source, fingerprint, policy = keyset()
    etag = await install(pins, source, fingerprint)
    replacement = jwk(Ed25519PrivateKey.generate(), "policy-nieuw")
    header = b64(canonical({"alg": "EdDSA", "kid": "policy", "typ": "hub-key-rotation+jws"}))
    body = b64(canonical({
        "type": "hub_key_rotation", "iss": "hub", "sequence": 1, "issued_at": iso(datetime.now(UTC)),
        "purpose": "policy", "add": [replacement], "retire": [],
    }))
    message = f"{header}.{body}"
    data = {"mode": "rotation", "source": json.dumps({
        "items": [{"sequence": 1, "statement": f"{message}.{b64(policy.sign(message.encode('ascii')))}"}],
    })}
    invalid = {"mode": "rotation", "source": '{"items":[{"sequence":2,"statement":"ongeldig"}]}'}
    rejected = await preview(pins, etag, invalid)
    assert (rejected.status_code, rejected.json()["code"]) == (422, "sleutelrotatie_ongeldig")
    pending = await preview(pins, etag, data)
    assert pending.status_code == 200
    expected = parse_pasted_keyset(pasted(
        json.loads(source)["jwks"]["keys"], [jwk(policy, "policy"), replacement],
    )).fingerprint
    assert pending.json() == {"fingerprint": expected, "sequence": "1"}
    assert (await pins.get(PATH)).json()["fingerprint"] == fingerprint
    assert (await put(pins, etag, {**data, "fingerprint": fingerprint})).status_code == 422
    assert (await pins.get(PATH)).headers["etag"] == etag
    saved = await put(pins, etag, {**data, "fingerprint": expected})
    assert saved.json() == {"state": "pinned", "fingerprint": expected, "sequence": "1"}


async def test_te_grote_sleutelset_wordt_voor_authenticatie_geweigerd(client):
    async def streamed():
        for _ in range(3):
            yield b" " * (1 << 19)

    problem = await client.post("/api/v1" + PATH + "/verify", content=streamed())
    assert (problem.status_code, problem.json()["code"]) == (413, "payload_too_large")


async def test_beschadigde_pin_herstellen_uit_nieuwe_sessie_en_vervanging_detecteren(pins, app, monkeypatch):
    source, fingerprint, policy = keyset()
    await install(pins, source, fingerprint)
    async with sessionmaker().begin() as db:
        (await db.get(ConnectorHubPin, 1)).policy_keys = []

    sessions = sessionmaker()
    now = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr(connector, "utcnow", lambda: now)
    with pytest.raises(HTTPException) as denied:
        await apply(sessions, envelope(policy, issued=now, expires=now + timedelta(minutes=1)), forbidden_operation)
    assert (denied.value.status_code, denied.value.detail["code"]) == (503, "connector_pins_unavailable")

    stale = (await pins.get(PATH)).headers["etag"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as fresh:
        login = await fresh.post("/api/v1/auth/login", json={"username": "beheer", "password": ADMIN_PW})
        assert login.status_code == 200
        session = Session(fresh, login.json()["csrf_token"])
        damaged = await session.get(PATH)
        assert damaged.json() == {"state": "damaged"}
        assert damaged.headers["etag"] == stale
        data = {"mode": "recovery", "source": json.dumps({**json.loads(source), "rotation_sequence": 0})}
        assert (await preview(session, stale, {**data, "mode": "paste"})).status_code == 409
        assert (await preview(session, stale, data)).json()["fingerprint"] == fingerprint
        assert (await put(session, stale, {**data, "fingerprint": "0000"})).status_code == 422
        with pytest.raises(HTTPException) as still_denied:
            await apply(sessions, envelope(policy, issued=now, expires=now + timedelta(minutes=1)), forbidden_operation)
        assert still_denied.value.status_code == 503
        confirmation = {**data, "fingerprint": fingerprint}
        replies = await asyncio.gather(put(session, stale, confirmation), put(pins, stale, confirmation))
        saved, replaced = sorted(replies, key=lambda response: response.status_code)
        assert (saved.status_code, saved.json()["state"]) == (200, "pinned")
        assert (replaced.status_code, replaced.json()["code"]) == (412, "precondition_failed")
    assert (await pins.get(PATH)).json()["fingerprint"] == fingerprint

    with pytest.raises(HTTPException) as old_request:
        await apply(sessions, envelope(policy, issued=now, expires=now + timedelta(minutes=1)), forbidden_operation)
    assert old_request.value.status_code == 403
    after = now + timedelta(seconds=1)
    monkeypatch.setattr(connector, "utcnow", lambda: after)

    async def simulate(*_):
        return 202, {"status": "simulatie"}

    response = await apply(sessions, envelope(policy, issued=after, expires=after + timedelta(minutes=1)), simulate)
    assert response == (202, {"status": "simulatie"}, False)


@pytest.mark.parametrize("sequence", [None, 0, 1], ids=["Hub-kopie", "oudere-volgorde", "actuele-volgorde"])
async def test_herstel_bewaakt_rotatievolgorde(pins, sequence):
    source, fingerprint, old_policy = keyset()
    etag = await install(pins, source, fingerprint)
    current_policy = jwk(Ed25519PrivateKey.generate(), "policy-nieuw")
    item = rotation(old_policy, "policy", 1, "policy", add=[current_policy], retire=["policy"])
    clipboard = {**json.loads(source), "policy_public_key": current_policy}
    if sequence is not None:
        clipboard["rotation_sequence"] = sequence
    clipboard_text = json.dumps(clipboard)
    current_fingerprint = parse_pasted_keyset(clipboard_text).fingerprint
    rotated = await put(pins, etag, {
        "mode": "rotation", "source": json.dumps({"items": [item]}), "fingerprint": current_fingerprint,
    })
    assert rotated.status_code == 200 and rotated.json()["sequence"] == "1"
    async with sessionmaker().begin() as db:
        (await db.get(ConnectorHubPin, 1)).fingerprint = "beschadigd"
    status = await pins.get(PATH)
    assert status.json() == {"state": "damaged"}

    damaged_etag = status.headers["etag"]
    repair = {"mode": "recovery", "source": clipboard_text}
    checked = await preview(pins, damaged_etag, repair)
    saved = await put(pins, damaged_etag, {**repair, "fingerprint": current_fingerprint})
    after = await pins.get(PATH)
    if sequence == 1:
        assert checked.status_code == saved.status_code == 200
        assert after.json() == {"state": "pinned", "sequence": "1", "fingerprint": current_fingerprint}
    else:
        assert checked.status_code == saved.status_code == (422 if sequence is None else 409)
        assert after.json() == {"state": "damaged"}
        assert after.headers["etag"] == damaged_etag


async def test_gelijktijdige_eerste_pin_houdt_het_http_contract(pins, monkeypatch):
    etag = (await pins.get(PATH)).headers["etag"]
    barrier = asyncio.Barrier(2)
    save = connector._save_pin

    async def synchronized(db, keyset, old, accept_from, **kwargs):
        assert old is None
        await asyncio.wait_for(barrier.wait(), timeout=5)
        return await save(db, keyset, old, accept_from, **kwargs)

    monkeypatch.setattr(connector, "_save_pin", synchronized)
    candidates = [
        {"mode": "paste", "source": source, "fingerprint": fingerprint}
        for source, fingerprint, _ in (keyset(), keyset())
    ]
    winner, loser = sorted(
        await asyncio.gather(*(put(pins, etag, candidate) for candidate in candidates)),
        key=lambda response: response.status_code,
    )
    current = await pins.get(PATH)
    assert current.json() == winner.json()
    assert (winner.status_code, loser.status_code) == (200, 412)
    assert loser.json()["code"] == "precondition_failed"
    assert loser.json()["details"]["huidige_etag"] == current.headers["etag"] == winner.headers["etag"]
    async with sessionmaker()() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorHubPin)) == 1
