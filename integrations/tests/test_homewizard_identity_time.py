"""Items 15, 21, 24 and 28 (HomeWizard): relay state, DST-safe timestamps, serial check, health."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import respx
from conftest import load

from shc_integrations.base import IdentityMismatch, StateUpdate
from shc_integrations.homewizard import HomeWizardAdapter, normalize_v1, normalize_v2
from shc_integrations.homewizard.adapter import resolve_device_time

TZ = ZoneInfo("Europe/Amsterdam")


def naive_local(utc: datetime) -> str:
    return utc.astimezone(TZ).replace(tzinfo=None).isoformat()


def v2_at(ts: str, received: datetime) -> datetime:
    rs = normalize_v2("p1", {"timestamp": ts, "power_w": 100}, received, TZ)
    return rs[0].observed_at


# --- item 21: zone-less timestamps ---------------------------------------------------------------


def test_autumn_double_hour_resolves_to_the_right_instant():
    """25 Oct 2026: 02:00-03:00 local happens twice (CEST, then CET)."""
    received = [datetime(2026, 10, 24, 23, 50, tzinfo=UTC) + timedelta(minutes=10 * i) for i in range(14)]
    resolved = []
    for r in received:
        device_time = r - timedelta(seconds=2)  # reading taken just before we received it
        resolved.append(v2_at(naive_local(device_time), r))
        assert resolved[-1] == device_time
    assert resolved == sorted(resolved) and len(set(resolved)) == len(resolved)  # no jumps back
    # Same wall clock, an hour apart: both resolved correctly.
    assert v2_at("2026-10-25T02:30:00", datetime(2026, 10, 25, 0, 30, 5, tzinfo=UTC)) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )
    assert v2_at("2026-10-25T02:30:00", datetime(2026, 10, 25, 1, 30, 5, tzinfo=UTC)) == datetime(
        2026, 10, 25, 1, 30, tzinfo=UTC
    )


def test_spring_gap_and_late_device_clock():
    """29 Mar 2026: 02:00-03:00 local does not exist."""
    for r in [datetime(2026, 3, 29, 0, 50, tzinfo=UTC), datetime(2026, 3, 29, 1, 0, 5, tzinfo=UTC)]:
        assert v2_at(naive_local(r), r) == r
    # A device that has not switched yet still says 02:30 (does not exist): closest real instant.
    assert v2_at("2026-03-29T02:30:00", datetime(2026, 3, 29, 1, 30, 2, tzinfo=UTC)) == datetime(
        2026, 3, 29, 1, 30, tzinfo=UTC
    )
    assert v2_at("2026-03-29T03:30:00", datetime(2026, 3, 29, 1, 30, 2, tzinfo=UTC)) == datetime(
        2026, 3, 29, 1, 30, tzinfo=UTC
    )


def test_device_clock_more_than_an_hour_off_falls_back_to_receive_time():
    received = datetime(2026, 11, 8, 13, 15, tzinfo=UTC)
    assert v2_at("2026-11-08T10:00:00", received) == received
    assert v2_at("2026-11-08T16:30:00", received) == received
    assert v2_at("2026-11-08T14:12:34+01:00", received) == datetime(2026, 11, 8, 13, 12, 34, tzinfo=UTC)
    assert v2_at("garbage", received) == received


def test_gas_readings_may_be_old_but_not_in_the_future():
    received = datetime(2026, 11, 8, 13, 10, tzinfo=UTC)
    data = {
        "timestamp": naive_local(received),
        "external": [{"type": "gas_meter", "timestamp": "2026-11-08T13:00:00", "value": 1.0}],
    }
    gas = next(r for r in normalize_v2("p1", data, received, TZ) if r.metric == "gas_m3")
    assert gas.observed_at == datetime(2026, 11, 8, 12, 0, tzinfo=UTC)  # 70 min old (hourly DSMR4) is kept
    data["external"][0]["timestamp"] = "2026-11-08T18:00:00"
    gas = next(r for r in normalize_v2("p1", data, received, TZ) if r.metric == "gas_m3")
    assert gas.observed_at == received


def test_v1_gas_timestamp_in_the_double_hour():
    first = normalize_v1("p1", {"total_gas_m3": 1.0, "gas_timestamp": 261025023000}, _utc(0, 31), TZ)
    second = normalize_v1("p1", {"total_gas_m3": 1.0, "gas_timestamp": 261025023000}, _utc(1, 31), TZ)
    assert first[0].observed_at == _utc(0, 30) and second[0].observed_at == _utc(1, 30)


def _utc(h, m):
    return datetime(2026, 10, 25, h, m, tzinfo=UTC)


def test_resolve_without_receive_time_keeps_old_behaviour():
    assert resolve_device_time(datetime(2026, 11, 8, 14, 0), TZ, None) == datetime(2026, 11, 8, 13, 0, tzinfo=UTC)


# --- item 24: serial check -----------------------------------------------------------------------


def p1(plain_factory, **cfg):
    return HomeWizardAdapter(
        {"host": "10.0.0.4", "serial": "3c39e7000001", "product_type": "HWE-P1", **cfg}, http_factory=plain_factory
    )


@respx.mock
async def test_other_device_on_the_address_raises_identity_mismatch(plain_factory):
    respx.get("http://10.0.0.4/api").respond(json={"product_type": "HWE-SKT", "serial": "3c39e70000ff"})
    data = respx.get("http://10.0.0.4/api/v1/data").respond(json=load("homewizard/v1_data_p1.json"))
    a = p1(plain_factory)
    with pytest.raises(IdentityMismatch) as ei:
        await a.poll()
    assert ei.value.code == "identity_mismatch"
    assert ei.value.diagnostics["found_serial"] == "3c39e70000ff"
    assert not data.called  # no readings of the wrong meter
    h = await a.health()
    assert h.state.value == "error" and h.code == "identity_mismatch"
    assert h.extra["identity_verified"] is False and h.extra["found_serial"] == "3c39e70000ff"
    assert "ander HomeWizard-apparaat" in h.detail


@respx.mock
async def test_serial_checked_every_poll_by_default(plain_factory):
    api = respx.get("http://10.0.0.4/api").respond(json=load("homewizard/v1_api_p1.json"))
    respx.get("http://10.0.0.4/api/v1/data").respond(json=load("homewizard/v1_data_p1.json"))
    a = p1(plain_factory, min_poll_interval_s=0)
    await a.poll()
    await a.poll()
    assert api.call_count == 2
    h = await a.health()
    assert h.state.value == "ok" and h.extra["identity_verified"] is True and "serienummer" in h.detail


@respx.mock
async def test_serial_check_interval_is_configurable(plain_factory):
    api = respx.get("http://10.0.0.4/api").respond(json=load("homewizard/v1_api_p1.json"))
    respx.get("http://10.0.0.4/api/v1/data").respond(json=load("homewizard/v1_data_p1.json"))
    a = p1(plain_factory, min_poll_interval_s=0, identity_check_interval_s=300)
    await a.poll()
    await a.poll()
    assert api.call_count == 1


@respx.mock
async def test_repair_with_expected_serial_detects_other_device(plain_factory):
    respx.get("http://10.0.0.5/api").respond(json=load("homewizard/v1_api_socket.json"))
    with pytest.raises(IdentityMismatch):
        await HomeWizardAdapter({}, http_factory=plain_factory).pair("10.0.0.5", expected_serial="3c39e7000001")


@respx.mock
async def test_legacy_config_without_serial_is_not_rejected(plain_factory):
    respx.get("http://10.0.0.4/api").respond(json=load("homewizard/v1_api_p1.json"))
    respx.get("http://10.0.0.4/api/v1/data").respond(json=load("homewizard/v1_data_p1.json"))
    a = HomeWizardAdapter({"host": "10.0.0.4", "product_type": "HWE-P1"}, http_factory=plain_factory)
    assert await a.poll()
    assert (await a.health()).extra["identity_verified"] is None


# --- item 15: relay state ------------------------------------------------------------------------


@respx.mock
async def test_socket_reports_relay_off_and_switch_lock(plain_factory):
    respx.get("http://10.0.0.5/api").respond(json=load("homewizard/v1_api_socket.json"))
    respx.get("http://10.0.0.5/api/v1/data").respond(json=load("homewizard/v1_data_socket.json"))
    respx.get("http://10.0.0.5/api/v1/state").respond(json={"power_on": False, "switch_lock": True, "brightness": 255})
    a = HomeWizardAdapter(
        {"host": "10.0.0.5", "serial": "3c39e7000002", "product_type": "HWE-SKT"}, http_factory=plain_factory
    )
    st = next(e for e in await a.poll() if isinstance(e, StateUpdate))
    assert st.device_identity == "3c39e7000002"
    assert st.state == {"relay_on": False, "switch_lock": True, "relay_state": "ok", "led_brightness": 255}


@respx.mock
async def test_meter_without_relay_has_no_relay_state(plain_factory):
    respx.get("http://10.0.0.4/api").respond(json=load("homewizard/v1_api_p1.json"))
    respx.get("http://10.0.0.4/api/v1/data").respond(json=load("homewizard/v1_data_p1.json"))
    assert not any(isinstance(e, StateUpdate) for e in await p1(plain_factory).poll())


@respx.mock
@pytest.mark.parametrize("v1_status", [200, 404])
async def test_v2_socket_reads_relay_via_v1_without_token_or_reports_unsupported(plain_factory, v1_status):
    respx.get("https://10.0.0.6/api").respond(json={"product_type": "HWE-SKT", "serial": "s6"})
    respx.get("https://10.0.0.6/api/measurement").respond(json={"power_w": 5.0, "energy_import_kwh": 1.0})
    state = respx.get("http://10.0.0.6/api/v1/state").respond(
        v1_status, json={"power_on": True, "switch_lock": True} if v1_status == 200 else {}
    )
    a = HomeWizardAdapter(
        {"host": "10.0.0.6", "serial": "s6", "product_type": "HWE-SKT", "api_version": "v2"},
        {"token": "SECRETTOKEN", "cert_fingerprint": "ab" * 32},
        http_factory=plain_factory,
    )
    st = next(e for e in await a.poll() if isinstance(e, StateUpdate)).state
    assert "authorization" not in state.calls.last.request.headers  # token never over plain HTTP
    if v1_status == 200:
        assert st["relay_on"] is True and st["switch_lock"] is True and st["relay_state"] == "ok"
    else:
        assert st == {"relay_on": None, "switch_lock": None, "relay_state": "unsupported"}


async def test_health_not_configured_has_extra():
    h = await HomeWizardAdapter({}).health()
    assert h.state.value == "not_configured" and isinstance(h.extra, dict)
