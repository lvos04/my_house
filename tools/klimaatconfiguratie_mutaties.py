#!/usr/bin/env python3
"""Mutatiebewijs voor de vroege klimaatconfiguratiecontroles en OEM-terugval."""

import handhaving_mutaties as harness

harness.TEST_FILE = "test_inbedrijfstelling_klimaat.py"
harness.CASES = {
    route: f"test_klimaatconfiguratie_tot_adapters[untested-{route}]"
    for route in ("schema", "beleid", "hervatten", "profiel", "profielwijziging")
}
harness.CASES["oem"] = "test_oem_regelt_bij_ongeteste_unit_en_schema_werkt_na_bevestiging"
harness.CASES["zonekoppeling"] = "test_zonekoppeling_kan_regeling_niet_naar_ongetest_doel_verleggen[bind]"
harness.PLUGIN = '''
import inspect
import textwrap
import pytest

@pytest.fixture(autouse=True)
def beschadig(monkeypatch):
    from shc_api.routers import profiles, zones
    from shc_api.worker.climate_loop import ClimateMixin

    action = MUTATION
    routes = {
        "schema": zones.put_schedule,
        "beleid": zones.put_policy,
        "hervatten": zones.resume,
        "profiel": profiles.apply_to_zone,
        "profielwijziging": profiles.update_profile,
        "oem": ClimateMixin._zone_round,
        "zonekoppeling": zones.patch_zone,
    }
    calls = {
        "profielwijziging": 'await enforce_zone_configuration(db, *zones)',
        "oem": 'await check_zone_configuration(db, z)',
        "zonekoppeling": 'check_command(await command_facts(db, unit), "set_hvac", {})',
    }
    func = routes[action]
    source = textwrap.dedent(inspect.getsource(func))
    call = calls.get(action, 'await enforce_zone_configuration(db, z)')
    assert call in source, "De mutatie vond haar controle niet."
    namespace = {}
    exec(compile(source.replace(call, 'pass', 1), "<klimaatmutatie>", "exec"), func.__globals__, namespace)
    monkeypatch.setattr(func, "__code__", namespace[func.__name__].__code__)
'''


if __name__ == "__main__":
    raise SystemExit(harness.main())
