import json

import httpx
import pytest
import respx
from conftest import load, no_sleep

from shc_integrations.base import (
    ButtonEvent,
    Command,
    PairingInstructions,
    PairResult,
    StateUpdate,
    Unsupported,
)
from shc_integrations.hue import HueAdapter, parse_sse
from shc_integrations.hue.adapter import events_from_containers

HOST = "192.168.1.30"
BASE = f"https://{HOST}"
LIGHT = "1d6c6a4c-0000-4000-8000-000000000001"


async def fp(_host):
    return "ab" * 32


def adapter(plain_factory, creds=None):
    return HueAdapter(
        {"host": HOST},
        creds or {"application_key": "k", "cert_fingerprint": "ab" * 32},
        http_factory=plain_factory,
        sleep=no_sleep,
        fingerprint_fetcher=fp,
    )


@respx.mock
async def test_pair_link_button_not_pressed_returns_instructions(plain_factory):
    respx.get(f"{BASE}/api/0/config").respond(json=load("hue/config.json"))
    respx.post(f"{BASE}/api").respond(json=load("hue/pair_link_not_pressed.json"))
    res = await adapter(plain_factory, {}).pair(HOST)
    assert isinstance(res, PairingInstructions)
    assert any("link-knop" in s for s in res.steps)


@respx.mock
async def test_pair_success_stores_key_and_pin(plain_factory):
    respx.get(f"{BASE}/api/0/config").respond(json=load("hue/config.json"))
    route = respx.post(f"{BASE}/api").respond(json=load("hue/pair_success.json"))
    res = await adapter(plain_factory, {}).pair(HOST)
    assert isinstance(res, PairResult)
    assert res.stable_identity == "ecb5fafffe000001"
    assert res.credentials["application_key"].startswith("doc-example")
    assert res.credentials["cert_fingerprint"] == "ab" * 32
    body = json.loads(route.calls.last.request.content)
    assert body == {"devicetype": "smart_home_control#server", "generateclientkey": True}


@respx.mock
async def test_poll_and_capabilities(plain_factory):
    route = respx.get(f"{BASE}/clip/v2/resource").respond(json=load("hue/resources.json"))
    respx.get(f"{BASE}/api/k/rules").respond(json={})
    a = adapter(plain_factory)
    events = await a.poll()
    assert route.calls.last.request.headers["hue-application-key"] == "k"
    states = {e.device_identity: e.state for e in events if isinstance(e, StateUpdate)}
    assert states[LIGHT]["on"] is True and states[LIGHT]["brightness"] == 60.0
    caps = await a.get_capabilities(LIGHT)
    assert {c.value for c in caps} == {"power", "brightness"}
    assert len(a.resources("scene")) == 1


@respx.mock
async def test_set_light_body_and_readback(plain_factory):
    put = respx.put(f"{BASE}/clip/v2/resource/light/{LIGHT}").respond(json={"data": [{"rid": LIGHT}]})
    respx.get(f"{BASE}/clip/v2/resource/light/{LIGHT}").respond(json=load("hue/light_after.json"))
    res = await adapter(plain_factory).execute(Command(LIGHT, "set_light", {"on": True, "brightness": 40}))
    assert json.loads(put.calls.last.request.content) == {"on": {"on": True}, "dimming": {"brightness": 40.0}}
    assert res.confirmed is True


@respx.mock
async def test_set_light_readback_mismatch_not_confirmed(plain_factory):
    respx.put(f"{BASE}/clip/v2/resource/light/{LIGHT}").respond(json={"data": []})
    respx.get(f"{BASE}/clip/v2/resource/light/{LIGHT}").respond(json=load("hue/light_after.json"))
    res = await adapter(plain_factory).execute(Command(LIGHT, "set_light", {"brightness": 90}))
    assert res.ok and not res.confirmed


@respx.mock
async def test_recall_scene_body(plain_factory):
    sid = "s0000000-0000-4000-8000-000000000001"
    put = respx.put(f"{BASE}/clip/v2/resource/scene/{sid}").respond(json={"data": []})
    respx.get(f"{BASE}/clip/v2/resource/scene/{sid}").respond(
        json={"errors": [], "data": [{"id": sid, "type": "scene", "status": {"active": "static"}}]}
    )
    res = await adapter(plain_factory).execute(Command(sid, "recall_scene", {}))
    assert json.loads(put.calls.last.request.content) == {"recall": {"action": "active"}}
    assert res.ok and res.confirmed  # readback via scene.status.active


async def test_unknown_action(plain_factory):
    with pytest.raises(Unsupported):
        await adapter(plain_factory).execute(Command(LIGHT, "explode", {}))


def test_eventstream_parsing():
    msgs = parse_sse(load("hue/eventstream.txt"))
    assert [m[0] for m in msgs] == ["1731070800:0", "1731070801:0", "1731070802:0"]
    events = [e for _id, c in msgs for e in events_from_containers(c)]
    light = events[0]
    assert isinstance(light, StateUpdate) and light.state["on"] is False
    btn = next(e for e in events if isinstance(e, ButtonEvent) and e.event == "initial_press")
    assert btn.device_identity == "2f1a0000-0000-4000-8000-0000000000d9"
    assert btn.event_id == "e2:b0000000-0000-4000-8000-000000000001"  # eventstream event id + resource id
    rot = next(e for e in events if isinstance(e, ButtonEvent) and e.event.startswith("rotate"))
    assert rot.event == "rotate_clock_wise"
    conn = events[-1]
    assert isinstance(conn, StateUpdate) and conn.online is False


@respx.mock
async def test_health_unauthorized(plain_factory):
    respx.get(f"{BASE}/clip/v2/resource/bridge").respond(status_code=403)
    h = await adapter(plain_factory).health()
    assert h.state.value == "error"


async def test_health_not_configured(plain_factory):
    h = await adapter(plain_factory, {"cert_fingerprint": "x"}).health()
    assert h.state.value == "not_configured"


@respx.mock
async def test_offline_raises_device_offline(plain_factory):
    from shc_integrations.base import DeviceOffline

    respx.get(f"{BASE}/clip/v2/resource").mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(DeviceOffline):
        await adapter(plain_factory).poll()
