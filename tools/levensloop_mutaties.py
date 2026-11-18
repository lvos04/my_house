#!/usr/bin/env python3
"""Mutatiebewijs voor apparaatbewijs, verwijzingen, Zigbee-beheer en V2–V4."""

import handhaving_mutaties as harness

harness.TEST_FILE = "test_inbedrijfstelling_levensloop.py"
TRANSITION = "test_nieuwe_soort_of_reactivatie_vraagt_actueel_testbewijs"
harness.CASES = {
    "v2": "test_v2_binden_en_knopkoppeling_zijn_een_serialiseerbaar_paar[mapping]",
    "v3": "test_v3_vervangweigering_noemt_de_uitgezette_oude_koppeling",
    "v4": "test_v4_dode_stap_verhuist_niet_bij_vervangen_door_dezelfde_soort",
    "uuid": "test_vervangen_verhuist_alleen_de_uuid_verwijzing_zonder_testbewijs[hex]",
    "binding-geen-overerving": "test_binden_geeft_geen_testbewijs_van_het_vorige_apparaat_door",
    "oude-scene": "test_oude_geteste_scene_is_na_archiveren_of_vervangen_niet_uitvoerbaar[archived]",
    "zigbee-weigering": "test_permit_join_uitzondering_tot_de_zigbee_adapter[protected]",
    "zigbee-route-archief": "test_permit_join_uitzondering_tot_de_zigbee_adapter[archived]",
    "zigbee-worker-archief": "test_worker_hercontroleert_koppelen_op_het_actuele_bridge_doel[archief]",
    "zigbee-worker-soort": "test_worker_hercontroleert_koppelen_op_het_actuele_bridge_doel[andere-soort]",
    "upgrade-reading": "test_passieve_soortopwaardering_neemt_geen_testbewijs_mee[reading]",
    "upgrade-button": "test_passieve_soortopwaardering_neemt_geen_testbewijs_mee[button]",
}
harness.CASES.update({name: f"{TRANSITION}[{name}]" for name in (
    "archive-user", "archive-adapter", "upgrade-info", "upgrade-state", "pair-upgrade", "pair-archive",
)})
harness.PLUGIN = '''
import inspect
import textwrap
import pytest

@pytest.fixture(autouse=True)
def beschadig(monkeypatch):
    from shc_api import commanding, policy
    from shc_api.routers import devices
    from shc_api.worker.ingest import IngestMixin

    def wijzig(func, old, new):
        source = textwrap.dedent(inspect.getsource(func))
        assert old in source, "De mutatie vond haar controle niet."
        namespace = {}
        exec(compile(source.replace(old, new, 1), "<levensloopmutatie>", "exec"), func.__globals__, namespace)
        monkeypatch.setattr(func, "__code__", namespace[func.__name__].__code__)

    action = MUTATION
    if action == "v2":
        wijzig(devices.bind, 'await lock_commissioning(db)', 'pass')
    elif action == "v3":
        wijzig(devices._check_reference_policy, 'if moving_from is not None and button_reference:', 'if False:')
    elif action == "v4":
        wijzig(devices._check_step_kinds, 'if old.kind == new.kind:', 'if False:')
    elif action == "uuid":
        wijzig(devices._swap_step_ids, '_reference_uuid(step.get("device_id")) != old.id',
               'step.get("device_id") != str(old.id)')
    elif action == "binding-geen-overerving":
        wijzig(devices.bind, 'await audit(db, p.actor, "role_bound"',
               'd.compatibility = "tested"\\n    await audit(db, p.actor, "role_bound"')
    elif action == "oude-scene":
        wijzig(commanding.check_active_target, 'if device.lifecycle != "active":', 'if False:')
    elif action == "zigbee-weigering":
        wijzig(devices.zigbee_permit_join, 'raise api_error(409, exc.code, exc.message) from exc', 'raise')
    elif action == "zigbee-route-archief":
        wijzig(devices.zigbee_permit_join, ', Device.lifecycle == "active"', '')
    elif action == "zigbee-worker-archief":
        wijzig(commanding.check_active_target, 'if device.lifecycle != "active":', 'if False:')
    elif action == "zigbee-worker-soort":
        wijzig(policy.check_command, 'if action == "permit_join" and device.kind != "zigbee_bridge":', 'if False:')
    elif action == "pair-archive":
        wijzig(devices.pair, 'elif d.lifecycle != "active":', 'elif False:')
    else:
        functions = {
            "archive-user": devices.patch_device,
            "archive-adapter": IngestMixin._device,
            "upgrade-info": IngestMixin._on_info,
            "upgrade-state": IngestMixin._on_state,
            "upgrade-reading": IngestMixin._on_reading,
            "upgrade-button": IngestMixin.on_button,
            "pair-upgrade": devices.pair,
        }
        target = 'dev' if action == "upgrade-button" else 'd'
        wijzig(functions[action], f'invalidate_device_test({target})', 'pass')
'''


if __name__ == "__main__":
    raise SystemExit(harness.main())
