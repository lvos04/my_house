"""Items 26, 27 and 28 (Hue adapter): PUT errors, ids, scene readback, health, eventstream watchdog."""

import asyncio
import json

import httpx
import pytest
import respx
from conftest import no_sleep
from hue_inventory import B1, D_DIMMER, G_LIVING, L1, V1_RULES, full_resources

from shc_integrations.base import AdapterError, ButtonEvent, Command, DeviceInfo, StateUpdate
from shc_integrations.hue import HueAdapter
from shc_integrations.hue import adapter as hue_adapter_mod

HOST = "192.168.1.30"
BASE = f"https://{HOST}"
CREDS = {"application_key": "k", "cert_fingerprint": "ab" * 32, "bridge_id": "ecb5fafffe000001"}


def make(plain_factory, **cfg):
    return HueAdapter({"host": HOST, **cfg}, dict(CREDS), http_factory=plain_factory, sleep=no_sleep)


def mock_inventory(rules=None):
    respx.get(f"{BASE}/clip/v2/resource").respond(json={"errors": [], "data": full_resources()})
    return respx.get(f"{BASE}/api/k/rules").respond(json=V1_RULES if rules is None else rules)


@respx.mock
async def test_poll_emits_inventory_and_derived_state(plain_factory):
    mock_inventory()
    a = make(plain_factory)
    evs = await a.poll()
    infos = {e.device_identity: e for e in evs if isinstance(e, DeviceInfo)}
    states = {e.device_identity: e for e in evs if isinstance(e, StateUpdate)}
    assert infos[D_DIMMER].kind == "button" and states[D_DIMMER].state["bridge_rules"]
    assert states[G_LIVING].state["lights_total"] == 2 and states[G_LIVING].state["scene"] == "Avond"
    assert evs.index(infos[L1]) < evs.index(states[L1])  # inventory before state
    assert a.v1_rules_available is True and a.device_info(D_DIMMER).name == "Dimmer gang"
    assert a._resources is a.model.resources  # the worker reads this dict


@respx.mock
async def test_v1_rules_unavailable_is_not_fatal(plain_factory):
    mock_inventory(rules=[{"error": {"type": 1, "description": "unauthorized user"}}])
    a = make(plain_factory)
    assert await a.poll()
    assert a.v1_rules_available is False


@respx.mock
async def test_put_errors_mean_not_confirmed_even_if_readback_matches(plain_factory):
    respx.put(f"{BASE}/clip/v2/resource/light/{L1}").respond(
        207,
        json={
            "data": [{"rid": L1, "rtype": "light"}],
            "errors": [{"description": "device (Plafond) has communication issues, command (.on) may not have effect"}],
        },
    )
    respx.get(f"{BASE}/clip/v2/resource/light/{L1}").respond(
        json={"errors": [], "data": [{"id": L1, "type": "light", "on": {"on": True}}]}
    )
    res = await make(plain_factory).execute(Command(L1, "set_light", {"on": True}))
    assert res.ok is True and res.confirmed is False
    assert "communication issues" in res.detail and res.detail.startswith("Hue Bridge meldt")


@respx.mock
async def test_put_rejected_raises_with_bridge_text(plain_factory):
    respx.put(f"{BASE}/clip/v2/resource/grouped_light/{G_LIVING}").respond(
        404, json={"data": [], "errors": [{"description": "Not Found"}]}
    )
    with pytest.raises(AdapterError, match="Not Found"):
        await make(plain_factory).execute(Command(G_LIVING, "set_group", {"on": False}))


@respx.mock
async def test_invalid_id_is_refused_before_sending(plain_factory):
    route = respx.put(url__regex=r".*").respond(json={})
    with pytest.raises(AdapterError, match="Ongeldige Hue-id"):
        await make(plain_factory).execute(Command("../../api/config", "set_light", {"on": True}))
    assert not route.called


@respx.mock
async def test_scene_without_status_is_not_confirmed(plain_factory):
    sid = "44444444-0000-4000-8000-000000000001"
    respx.put(f"{BASE}/clip/v2/resource/scene/{sid}").respond(json={"data": [], "errors": []})
    respx.get(f"{BASE}/clip/v2/resource/scene/{sid}").respond(json={"data": [{"id": sid, "type": "scene"}]})
    res = await make(plain_factory).execute(Command(sid, "recall_scene", {}))
    assert res.ok and not res.confirmed and "niet welke scène" in res.detail


@respx.mock
async def test_health_fields_and_bridge_identity(plain_factory):
    route = respx.get(f"{BASE}/clip/v2/resource/bridge")
    route.respond(json={"data": [{"id": "x", "type": "bridge", "bridge_id": "ecb5fafffe000001"}]})
    a = make(plain_factory)
    h = await a.health()
    assert h.state.value == "ok"
    assert set(h.extra) >= {"eventstream_connected", "connected_since", "last_event_at", "bridge_id"}
    json.dumps(h.extra)
    route.respond(json={"data": [{"id": "x", "type": "bridge", "bridge_id": "001788fffe000002"}]})
    h = await a.health()
    assert h.state.value == "error" and h.code == "identity_mismatch"


@respx.mock
async def test_health_warns_when_eventstream_down_too_long(plain_factory):
    from datetime import UTC, datetime, timedelta

    respx.get(f"{BASE}/clip/v2/resource/bridge").respond(json={"data": [{"bridge_id": "ecb5fafffe000001"}]})
    a = make(plain_factory)
    a.subscribing, a.stream_connected = True, False
    a.disconnected_since = datetime.now(UTC) - timedelta(minutes=5)
    h = await a.health()
    assert h.state.value == "warn" and h.code == "eventstream_disconnected"
    a.disconnected_since = datetime.now(UTC) - timedelta(seconds=30)  # brief reconnect: no warning
    assert (await a.health()).state.value == "ok"


def sse(msg_id, cid, data):
    payload = [{"creationtime": "2026-11-08T13:41:00Z", "id": cid, "type": "update", "data": data}]
    return f"id: {msg_id}\ndata: {json.dumps(payload)}\n\n".encode()


PRESS = {
    "id": B1,
    "type": "button",
    "owner": {"rid": D_DIMMER, "rtype": "device"},
    "button": {"button_report": {"event": "initial_press", "updated": "2026-11-08T13:41:00.100Z"}},
}


@respx.mock
async def test_eventstream_watchdog_reconnects_and_reloads(plain_factory):
    mock_inventory()
    respx.get(f"{BASE}/clip/v2/resource/bridge").respond(json={"data": [{"bridge_id": "ecb5fafffe000001"}]})

    async def silent_after_one_event():
        yield b": hi\n\n"
        yield sse("100:0", "evt-1", [PRESS])
        await asyncio.sleep(30)  # bridge lost power: no bytes, no FIN

    async def trickle():
        yield sse("101:0", "evt-2", [PRESS])
        for _ in range(10_000):  # bytes keep coming (for minutes) but never a complete message
            await asyncio.sleep(0.05)
            yield b"data: ["

    async def last():
        yield sse("102:0", "evt-3", [PRESS])

    stream = respx.get(f"{BASE}/eventstream/clip/v2")
    stream.side_effect = [
        httpx.Response(200, content=silent_after_one_event()),
        httpx.Response(200, content=trickle()),
        httpx.Response(200, content=last()),
    ]
    a = make(plain_factory, eventstream_read_timeout_s=0.3)
    got = []
    agen = a.subscribe()
    async with asyncio.timeout(5):  # only the watchdog can end the trickling stream this fast
        async for ev in agen:
            got.append(ev)
            if isinstance(ev, ButtonEvent) and ev.event_id.startswith("evt-3"):
                break
    h = await a.health()
    await agen.aclose()

    presses = [e.event_id for e in got if isinstance(e, ButtonEvent)]
    assert presses == [f"evt-1:{B1}", f"evt-2:{B1}", f"evt-3:{B1}"]
    reloads = [e for e in got if isinstance(e, DeviceInfo) and e.device_identity == D_DIMMER]
    assert len(reloads) == 3  # full reload after every (re)connect
    assert stream.call_count == 3 and a.connections == 3
    assert stream.calls[1].request.headers["last-event-id"] == "100:0"
    assert stream.calls[2].request.headers["last-event-id"] == "101:0"
    assert h.extra["eventstream_connected"] is True and h.extra["connected_since"] and h.extra["last_event_at"]
    assert a.stream_connected is False  # closed by aclose()
    assert a.stream_error is None  # a quiet stream is reconnected, not reported as an error


def test_default_factory_uses_tcp_keepalive(monkeypatch):
    seen = {}

    def fake_client_for(base, fp, **kw):
        seen.update(kw, base=base)
        return httpx.AsyncClient(base_url=base)

    monkeypatch.setattr(hue_adapter_mod, "client_for", fake_client_for)
    a = HueAdapter({"host": HOST}, dict(CREDS))
    a._stream_http()
    opts = seen["socket_options"]
    import socket

    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in opts and seen["base"] == BASE
