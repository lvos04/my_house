from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from shc_api import climate as c
from shc_api import energy as e
from shc_api.policy import DeviceFacts, PolicyDenied, check_command

AMS = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 11, 8, 13, 0, tzinfo=UTC)  # Sunday 14:00 local


def zone(**kw):
    base = dict(
        now=NOW,
        tz=AMS,
        policy=c.policy_with_defaults({}),
        caps=c.Capabilities(step_c=0.5),
        control="schedule",
        desired_mode="heat",
        desired_target_c=21.0,
        override_until=None,
        sensor=c.SensorValue(20.9, NOW - timedelta(minutes=2)),
        unit=c.UnitState(True, "heat", 21.0),
        schedule=[c.ScheduleEntry(tuple(range(7)), "07:00", 21.0, "heat")],
        last_command_at=NOW - timedelta(hours=2),
        last_commanded={"mode": "heat", "target_c": 21.0},
        start_times=[],
    )
    base.update(kw)
    return c.ZoneInput(**base)


# --- policy -----------------------------------------------------------------


@pytest.mark.parametrize("action", ["set_relay", "set_power", "switch", "power_off", "anything"])
def test_boiler_never_switchable(action):
    facts = DeviceFacts("energy_socket", frozenset({"boiler_energy"}), frozenset())
    with pytest.raises(PolicyDenied):
        check_command(facts, action, {"on": False})


def test_custom_protected_role_and_safe_mode():
    with pytest.raises(PolicyDenied):
        check_command(DeviceFacts("light", frozenset({"x"}), frozenset({"x"})), "set_light", {})
    with pytest.raises(PolicyDenied):
        check_command(DeviceFacts("ac_unit", frozenset(), frozenset(), safe_mode=True), "set_hvac", {})
    check_command(DeviceFacts("ac_unit", frozenset({"living_room_climate"}), frozenset()), "set_hvac", {})


def test_energy_socket_relay_blocked_even_without_role():
    with pytest.raises(PolicyDenied):
        check_command(DeviceFacts("energy_socket", frozenset(), frozenset()), "set_relay", {"on": True})


# --- climate ----------------------------------------------------------------


def test_in_band_no_change():
    d = c.decide(zone())
    assert d.send is None and d.reason == "no_change"


def test_cold_room_raises_setpoint_bounded():
    d = c.decide(zone(sensor=c.SensorValue(19.0, NOW)))
    assert d.send == {"mode": "heat", "target_c": 21.5}


def test_min_interval_respected():
    d = c.decide(zone(sensor=c.SensorValue(19.0, NOW), last_command_at=NOW - timedelta(minutes=5)))
    assert d.send is None and d.reason == "min_interval"


def test_stale_sensor_falls_back_to_oem_and_no_correction():
    d = c.decide(zone(sensor=c.SensorValue(15.0, NOW - timedelta(hours=1)), unit=c.UnitState(True, "heat", 21.0)))
    assert d.control == "oem_fallback" and d.send is None and not d.sensor_valid
    assert "verouderd" in d.notice


def test_implausible_sensor():
    d = c.decide(zone(sensor=c.SensorValue(85.0, NOW)))
    assert not d.sensor_valid


def test_remote_control_change_becomes_override():
    # A difference must be seen in two consecutive readbacks (verbeterlijst 11): this is the second.
    d = c.decide(zone(unit=c.UnitState(True, "heat", 24.0), mismatch_count=1))
    assert d.control == "manual" and d.override_source == "remote_control" and d.send is None


def test_remote_off_is_never_reactivated():
    d = c.decide(zone(unit=c.UnitState(True, "off", 21.0), mismatch_count=1))
    assert d.control == "off" and d.send is None
    d2 = c.decide(zone(control="off", unit=c.UnitState(True, "off", 21.0)))
    assert d2.send is None


def test_manual_override_respected_until_expiry():
    d = c.decide(zone(control="manual", override_until=NOW + timedelta(minutes=30), sensor=c.SensorValue(17, NOW)))
    assert d.send is None
    d = c.decide(zone(control="manual", override_until=NOW - timedelta(minutes=1), sensor=c.SensorValue(19.0, NOW)))
    assert d.control == "schedule" and d.send is not None


def test_shared_outdoor_conflict_blocks_cooling():
    z = zone(
        schedule=[c.ScheduleEntry(tuple(range(7)), "07:00", 22.0, "cool")],
        other_zone_modes=["heat"],
        unit=c.UnitState(True, "off", None),
        last_commanded={},
    )
    d = c.decide(z)
    assert d.send is None and d.reason == "mode_blocked"
    assert c.validate_request(c.Capabilities(), ["heat"], "cool", None)
    assert c.validate_request(c.Capabilities(), ["cool"], "heat", None)
    assert c.validate_request(c.Capabilities(), ["heat"], "heat", 21) is None
    assert c.validate_request(c.Capabilities(), [], None, 35)


def test_max_starts_prevents_rapid_cycling():
    z = zone(
        unit=c.UnitState(True, "off", None),
        last_commanded={},
        start_times=[NOW - timedelta(minutes=m) for m in (5, 20, 40)],
    )
    d = c.decide(z)
    assert d.send is None and d.reason == "max_starts"


def test_offline_unit_does_nothing():
    d = c.decide(zone(unit=c.UnitState(False, None, None)))
    assert d.send is None and d.reason == "unit_offline"


def test_schedule_lookup_wraps_week():
    sched = [c.ScheduleEntry((0,), "07:00", 20.0), c.ScheduleEntry((6,), "22:00", 17.0)]
    monday_early = datetime(2026, 11, 9, 4, 0, tzinfo=UTC)  # Monday 05:00 local
    assert c.scheduled_entry(sched, monday_early, AMS).target_c == 17.0
    at, entry = c.next_change(sched, monday_early, AMS)
    assert entry.target_c == 20.0 and at.astimezone(AMS).hour == 7


# --- energy -----------------------------------------------------------------


def P(h, m, kwh, day=8, month=11):
    return e.CounterPoint(datetime(2026, month, day, h, m, tzinfo=UTC), kwh)


def test_daily_total_from_cumulative():
    s, end, unit = e.period_bounds("day", date(2026, 11, 8), AMS)
    b = e.make_buckets(s, end, unit, AMS)
    assert len(b) == 24
    pts = [e.CounterPoint(s.astimezone(UTC) + timedelta(minutes=15 * i), 1000 + 0.1 * i) for i in range(97)]
    e.aggregate(pts, b)
    assert sum(x.kwh for x in b) == pytest.approx(9.6)
    assert all(x.complete for x in b)


def test_dst_day_has_25_hours():
    s, end, unit = e.period_bounds("day", date(2026, 10, 25), AMS)
    assert len(e.make_buckets(s, end, unit, AMS)) == 25
    s, end, unit = e.period_bounds("day", date(2026, 3, 29), AMS)
    assert len(e.make_buckets(s, end, unit, AMS)) == 23


def test_counter_reset_and_duplicates_and_gap():
    pts = [P(10, 0, 500.0), P(10, 0, 500.0), P(10, 15, 500.5), P(10, 30, 0.2), P(10, 45, 0.4), P(14, 0, 1.4)]
    d = e.deltas(pts)
    assert [round(x[2], 3) for x in d] == [0.5, 0.2, 0.2, 1.0]
    assert d[1][3] == "tellerreset gedetecteerd"
    s, end, unit = e.period_bounds("day", date(2026, 11, 8), AMS)
    b = e.make_buckets(s, end, unit, AMS)
    e.aggregate(pts, b)
    assert sum(x.kwh for x in b) == pytest.approx(1.9)
    assert any(not x.complete and "ontbrekende metingen" in x.notes for x in b)


def test_big_drop_is_not_negative_usage():
    d = e.deltas([P(10, 0, 500.0), P(10, 15, 300.0)])
    assert d[0][2] == 0.0


# --- adaptive & experiments --------------------------------------------------


def test_adaptive_fit_recovers_room_response():
    from shc_api import adaptive as ad

    k_true, c_true = 0.8, -0.2
    temp, sp = 18.0, 21.0
    samples = []
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    for i in range(200):
        samples.append(ad.Sample(t0 + timedelta(minutes=15 * i), temp, sp, True))
        sp = 21.0 if (i // 24) % 2 == 0 else 19.0
        temp += (k_true * (sp - temp) + c_true) * 0.25 + (0.02 if i % 3 else -0.02)
    m = ad.fit(samples)
    assert m and m["usable"] and abs(m["k_per_h"] - k_true) < 0.15
    off = ad.advised_offset(m, 21.0, 19.5, 0.5)
    assert 0 < off <= ad.MAX_ADAPTIVE_OFFSET
    assert ad.advised_offset(m, 21.0, 20.9, 0.5) == 0.0
    assert ad.fit(samples[:20]) is None


def test_experiment_comparison_is_honest():
    from shc_api.adaptive import PeriodStats, compare

    ok = compare([PeriodStats("Constant", 7, 21.0, 0.3, 4, 6.0), PeriodStats("Nachtverlaging", 7, 18.2, 0.5, 9, 6.8)])
    assert ok["verdict"] == "vergelijkbaar" and ok["rows"][1]["vs_first_pct"] == pytest.approx(-13.3, abs=0.1)
    weather = compare([PeriodStats("A", 7, 21.0, 0.3, 4, 2.0), PeriodStats("B", 7, 15.0, 0.3, 4, 9.0)])
    assert weather["verdict"] == "indicatief" and any("Buitentemperatuur" in r for r in weather["reasons"])
    no_out = compare([PeriodStats("A", 7, 21.0, 0.3, 4, None), PeriodStats("B", 3, 15.0, 0.3, 4, None)])
    assert no_out["verdict"] == "indicatief" and len(no_out["reasons"]) == 2


def test_adaptive_offset_is_bounded_by_policy_in_engine():
    z = zone(sensor=c.SensorValue(19.0, NOW), adaptive_offset_c=5.0)
    d = c.decide(z)
    assert d.send and d.send["target_c"] <= 21.0 + c.DEFAULT_POLICY["max_offset_c"]
