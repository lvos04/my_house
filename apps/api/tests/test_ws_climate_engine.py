"""Climate engine (pure): items 10–14, 30 and 45 of the verbeterlijst."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from shc_api import adaptive as ad
from shc_api import climate as c

AMS = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 11, 8, 13, 0, tzinfo=UTC)  # Sunday 14:00 local
ALL_WEEK = tuple(range(7))


def zone(**kw):
    base = dict(
        now=NOW,
        tz=AMS,
        policy=c.policy_with_defaults({}),
        caps=c.Capabilities(step_c=1.0),
        control="schedule",
        desired_mode="heat",
        desired_target_c=None,
        override_until=None,
        sensor=c.SensorValue(21.0, NOW - timedelta(minutes=2)),
        unit=c.UnitState(True, "heat", 21.0, NOW),
        schedule=[c.ScheduleEntry(ALL_WEEK, "07:00", 21.0, "heat")],
        last_command_at=NOW - timedelta(hours=2),
        last_commanded={"mode": "heat", "target_c": 21.0},
        start_times=[],
    )
    base.update(kw)
    return c.ZoneInput(**base)


# --- item 30: correction with step 1.0, even and odd targets -----------------------------


@pytest.mark.parametrize(("target", "room", "expect"), [(20.0, 19.0, 21.0), (21.0, 20.0, 22.0), (22.0, 21.4, 23.0)])
def test_step_one_moves_at_least_one_step_for_even_and_odd_targets(target, room, expect):
    d = c.decide(
        zone(
            schedule=[c.ScheduleEntry(ALL_WEEK, "07:00", target, "heat")],
            unit=c.UnitState(True, "heat", target, NOW),
            last_commanded={"mode": "heat", "target_c": target},
            sensor=c.SensorValue(room, NOW),
        )
    )
    assert d.send == {"mode": "heat", "target_c": expect}
    assert d.correction_c == pytest.approx(1.0)


def test_correction_is_kept_with_decimals_and_held_inside_the_band():
    # step 0.5, max step 2: the correction moves half the error, kept unrounded
    pol = c.policy_with_defaults({"max_step_c": 2.0})
    d = c.decide(zone(caps=c.Capabilities(step_c=0.5), policy=pol, sensor=c.SensorValue(19.6, NOW)))
    assert d.correction_c == pytest.approx(0.7) and d.send == {"mode": "heat", "target_c": 21.5}
    # room inside the band: the stored correction stays and the unit keeps its corrected setpoint
    d = c.decide(
        zone(
            caps=c.Capabilities(step_c=0.5),
            policy=pol,
            sensor=c.SensorValue(21.1, NOW),
            correction_c=0.7,
            unit=c.UnitState(True, "heat", 21.5, NOW),
            last_commanded={"mode": "heat", "target_c": 21.5},
        )
    )
    assert d.send is None and d.reason == "no_change" and d.correction_c == pytest.approx(0.7)


def test_overshoot_brings_the_correction_back_down():
    d = c.decide(
        zone(
            sensor=c.SensorValue(21.8, NOW),
            correction_c=1.0,
            unit=c.UnitState(True, "heat", 22.0, NOW),
            last_commanded={"mode": "heat", "target_c": 22.0},
        )
    )
    assert d.send == {"mode": "heat", "target_c": 21.0} and d.correction_c == pytest.approx(0.0)


def test_cooling_with_step_one_lowers_the_setpoint():
    d = c.decide(
        zone(
            schedule=[c.ScheduleEntry(ALL_WEEK, "07:00", 24.0, "cool")],
            unit=c.UnitState(True, "cool", 24.0, NOW),
            last_commanded={"mode": "cool", "target_c": 24.0},
            sensor=c.SensorValue(25.5, NOW),
        )
    )
    assert d.send == {"mode": "cool", "target_c": 23.0} and d.correction_c == pytest.approx(-1.0)


def test_held_back_change_does_not_wind_up_the_correction():
    d = c.decide(zone(sensor=c.SensorValue(19.0, NOW), last_command_at=NOW - timedelta(minutes=3), correction_c=1.0))
    assert d.reason == "min_interval" and d.correction_c is None


def test_round_to_step_never_rounds_to_even():
    assert c.round_to_step(20.5, 1.0) == 21.0
    assert c.round_to_step(21.5, 1.0) == 22.0
    assert c.round_to_step(23.5, 1.0, prefer_up=False) == 23.0
    assert c.round_to_step(21.26, 0.5) == 21.5


def test_invalid_sensor_resets_the_correction():
    d = c.decide(zone(sensor=None, correction_c=1.5))
    assert d.control == "oem_fallback" and d.correction_c == 0.0


# --- item 45: energy priority -------------------------------------------------------------


def test_energy_priority_widens_band_and_caps_costly_direction():
    room = c.SensorValue(20.7, NOW)  # 0.3 below target
    balanced = c.decide(zone(sensor=room))
    saving = c.decide(zone(sensor=room, policy=c.policy_with_defaults({"energy_priority": "saving"})))
    assert balanced.send is not None
    assert saving.send is None and saving.reason == "no_change"
    capped = c.decide(
        zone(
            sensor=c.SensorValue(18.0, NOW),
            correction_c=0.5,
            policy=c.policy_with_defaults({"energy_priority": "saving"}),
            unit=c.UnitState(True, "heat", 21.5, NOW),
            last_commanded={"mode": "heat", "target_c": 21.5},
        )
    )
    assert capped.correction_c == pytest.approx(1.0)  # max_offset 2.0, halved for heating when saving
    assert c.correction_limits(c.policy_with_defaults({"energy_priority": "saving"}), "cool") == (-1.0, 2.0)
    assert c.effective_band(c.policy_with_defaults({"energy_priority": "comfort"})) == pytest.approx(0.375)


# --- item 12: schedule blocks without a mode ------------------------------------------------


def test_block_without_mode_keeps_the_running_mode():
    d = c.decide(
        zone(
            schedule=[c.ScheduleEntry(ALL_WEEK, "07:00", 23.0)],
            unit=c.UnitState(True, "cool", 25.0, NOW),
            last_commanded={"mode": "cool", "target_c": 25.0},
            sensor=None,
        )
    )
    assert d.send == {"mode": "cool", "target_c": 24.0}  # toward 23, at most 1 °C per change


def test_block_without_mode_never_starts_an_off_unit():
    d = c.decide(
        zone(
            schedule=[c.ScheduleEntry(ALL_WEEK, "07:00", 21.0)],
            unit=c.UnitState(True, "off", 21.0, NOW),
            last_commanded={"mode": "off", "target_c": 21.0},
            desired_mode="heat",
        )
    )
    assert d.send is None and d.reason == "keep_off"


def test_no_schedule_leaves_the_unit_alone():
    d = c.decide(zone(schedule=[], unit=c.UnitState(True, "heat", 21.0, NOW)))
    assert d.send is None and d.reason == "no_schedule"


def test_off_block_switches_a_running_unit_off():
    d = c.decide(zone(schedule=[c.ScheduleEntry(ALL_WEEK, "07:00", 18.0, "off")]))
    assert d.send == {"mode": "off"} and d.reason == "schedule_off"


# --- items 10 and 11: external changes, pending commands ---------------------------------


def test_difference_needs_two_consecutive_readbacks():
    remote = c.UnitState(True, "heat", 24.0, NOW)
    first = c.decide(zone(unit=remote))
    assert first.reason == "checking_change" and first.mismatch_count == 1 and first.send is None
    assert first.control == "schedule"
    same_readback = c.decide(zone(unit=remote, mismatch_count=1, readback_new=False))
    assert same_readback.reason == "checking_change" and same_readback.mismatch_count == 1
    second = c.decide(zone(unit=remote, mismatch_count=1))
    assert second.control == "manual" and second.override_source == "remote_control"
    assert second.baseline == {"mode": "heat", "target_c": 24.0}


def test_glitch_resets_the_mismatch_count():
    d = c.decide(zone(mismatch_count=1))
    assert d.mismatch_count == 0 and d.reason == "no_change"


def test_pending_command_skips_detection_and_holds():
    d = c.decide(zone(unit=c.UnitState(True, "off", 21.0, NOW), pending=True, mismatch_count=1))
    assert d.control == "schedule" and d.reason == "pending" and d.send is None and d.mismatch_count == 0


def test_empty_baseline_adopts_what_the_unit_shows():
    d = c.decide(zone(last_commanded={}, unit=c.UnitState(True, "heat", 22.0, NOW)))
    assert d.baseline == {"mode": "heat", "target_c": 22.0}


def test_override_expiry_returns_to_schedule_without_redetecting():
    """Item 10: after detection the observed state is the baseline, so expiry goes back to schedule."""
    remote = c.UnitState(True, "heat", 24.0, NOW)
    d = c.decide(zone(unit=remote, mismatch_count=1))
    assert d.control == "manual"
    later = NOW + timedelta(hours=3)
    z2 = zone(
        now=later,
        control="manual",
        override_until=d.override_until,
        unit=c.UnitState(True, "heat", 24.0, later),
        last_commanded=d.baseline,
        sensor=c.SensorValue(21.0, later),
        last_command_at=later - timedelta(hours=3),
    )
    d2 = c.decide(z2)
    assert d2.control == "schedule" and d2.reason == "schedule"
    assert d2.send == {"mode": "heat", "target_c": 23.0}  # back toward 21, max jump 1 °C per change


def test_remote_change_during_off_becomes_manual():
    d = c.decide(
        zone(
            control="off",
            unit=c.UnitState(True, "heat", 22.0, NOW),
            last_commanded={"mode": "off"},
            mismatch_count=1,
        )
    )
    assert d.control == "manual" and d.override_source == "remote_control"


# --- item 14: outdoor-unit conflicts --------------------------------------------------------


def test_auto_conflicts_with_heat_and_cool():
    assert c.modes_conflict("auto", "heat") and c.modes_conflict("cool", "auto") and c.modes_conflict("dry", "heat")
    assert not c.modes_conflict("fan", "heat") and not c.modes_conflict("auto", "auto")
    blocked = {b["mode"] for b in c.blocked_modes(c.Capabilities(), ["auto"])}
    assert blocked == {"heat", "cool", "dry"}
    blocked = {b["mode"] for b in c.blocked_modes(c.Capabilities(), ["heat"])}
    assert blocked == {"cool", "dry", "auto"}
    assert c.validate_request(c.Capabilities(modes=("heat",)), ["heat"], "off", None) is None


def test_engine_respects_auto_on_the_other_unit():
    d = c.decide(zone(other_zone_modes=["auto"]))
    assert d.reason == "mode_blocked" and d.send is None


# --- watchdog / comfort score -----------------------------------------------------------------


def test_watchdog_helpers():
    assert c.watchdog_needed("heat", 21.0, 19.0, 0.5)
    assert not c.watchdog_needed("heat", 21.0, 20.9, 0.5)
    assert c.watchdog_needed("cool", 24.0, 26.0, 0.5)
    assert not c.watchdog_needed("fan", 24.0, 26.0, 0.5)
    assert c.watchdog_progress("heat", 19.0, 19.5) == pytest.approx(0.5)
    assert c.watchdog_progress("cool", 26.0, 26.2) == pytest.approx(-0.2)


def test_matches_command_for_late_confirmation():
    assert c.matches_command({"mode": "heat", "target_c": 21.0}, "heat", 21.0)
    assert not c.matches_command({"mode": "heat", "target_c": 21.0}, "off", 21.0)
    assert c.matches_command({"mode": "off"}, "off", None)
    assert not c.matches_command({"target_c": 22.0}, "heat", 21.0)


def test_comfort_score_is_time_weighted():
    t0 = NOW
    temps = [(t0, 21.0), (t0 + timedelta(minutes=10), 19.0), (t0 + timedelta(minutes=20), 21.1)]
    score = ad.comfort_share(temps, lambda _at: 21.0, 0.5, t0 + timedelta(minutes=30))
    assert score == pytest.approx(66.7)
    assert ad.comfort_share(temps, lambda _at: None, 0.5, t0 + timedelta(minutes=30)) is None


def test_experiment_comparison_flags_other_zone_confounder():
    rows = [
        ad.PeriodStats("A", 7, 21.0, 0.3, 4, 6.0, 90.0, 20.0),
        ad.PeriodStats("B", 7, 18.0, 0.4, 5, 6.2, 80.0, 22.0),
    ]
    res = ad.compare(rows)
    assert res["verdict"] == "indicatief" and any("andere ruimte" in r for r in res["reasons"])
    assert res["rows"][0]["comfort_pct"] == 90.0
