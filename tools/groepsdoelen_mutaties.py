#!/usr/bin/env python3
"""Mutatiebewijs voor ledenmetadata, voorrang en alle samengestelde ingangen."""

import handhaving_mutaties as harness

harness.TEST_FILE = "test_inbedrijfstelling_groepen.py"
harness.CASES = {
    "ledenstatus": "test_groepslid_weigert_geheel_zonder_mutatie",
    "onbekend": "test_groepslid_weigert_geheel_zonder_mutatie",
    "levensloop": "test_groepslid_weigert_geheel_zonder_mutatie",
    "soort": "test_groepslid_weigert_geheel_zonder_mutatie",
    "integratie": "test_groepslid_weigert_geheel_zonder_mutatie",
    "volledigheid": "test_onvolledige_ledenmetadata_blijft_dicht",
    "beschermingsvoorrang": "test_indirecte_bescherming_voor_alle_teststatussen",
    "sceneroute-voorrang": "test_indirecte_bescherming_voor_alle_teststatussen",
    "direct": "test_groepscontrole_voor_semantische_validatie",
    "wachtrij": "test_commandokern_toetst_leden_voor_schrijven",
    "worker": "test_worker_herleest_ieder_lid",
    "simulatie-e8": "test_e8_simulatie_toetst_groep_voor_verkeerde_modus",
    "inventaris": "test_inventaris_behoudt_leden_zonder_automatisch_getest",
    "adapter-leden": "test_groepslid_weigert_geheel_zonder_mutatie",
    "adapter-volledigheid": "test_onvolledige_hue_inventaris_weigert",
    "leden-woning": "test_andere_bron_bewijst_geen_groepslid",
    "leden-provider": "test_andere_bron_bewijst_geen_groepslid",
    "scene-woning": "test_scene_met_onbekende_groep_blijft_dicht",
    "scene-soort": "test_scene_met_onbekende_groep_blijft_dicht",
    "scene-integratie": "test_scene_met_onbekende_groep_blijft_dicht",
    "scene-verse-groep": "test_scene_leest_groepswoning_vers_voor_afscherming",
    "oude-params": "test_patch_koppeling_toetst_groep_voor_oude_ongeldige_params",
    "gate-uit-te-streng": "test_gate_uit_behoudt_oude_onbekende_groep",
}
harness.PLUGIN = '''
import inspect
import textwrap
import pytest

@pytest.fixture(autouse=True)
def beschadig(monkeypatch):
    from shc_api import commanding
    from shc_api.routers import lights
    from shc_api.worker.commands import CommandsMixin
    from shc_api.worker.ingest import IngestMixin
    from shc_integrations.hue import HueModel
    kind = MUTATION

    def wijzig(func, old, new):
        source = textwrap.dedent(inspect.getsource(func))
        assert old in source, "De mutatie vond haar controle niet."
        namespace = {}
        exec(compile(source.replace(old, new, 1), "<groepsmutatie>", "exec"), func.__globals__, namespace)
        monkeypatch.setattr(func, "__code__", namespace[func.__name__].__code__)

    func = commanding.check_scene_group_policy
    if kind == "ledenstatus":
        wijzig(func, 'for item in facts[1:]:', 'for item in []:')
    elif kind == "onbekend":
        wijzig(func, 'if unknown_members and', 'if False and')
    elif kind == "levensloop":
        wijzig(func, 'm.lifecycle != "active"', 'False')
    elif kind == "soort":
        wijzig(func, 'm.kind != "light"', 'False')
    elif kind == "integratie":
        wijzig(func, 'm.integration_id != group.integration_id', 'False')
    elif kind == "volledigheid":
        wijzig(func, 'state.get("members_complete") is not True', 'False')
    elif kind == "beschermingsvoorrang":
        wijzig(func, 'for item in facts:', 'for item in []:')
    elif kind == "sceneroute-voorrang":
        wijzig(lights.group_command, 'protection_only=body.scene_id is not None', 'protection_only=False')
    elif kind == "direct":
        wijzig(lights._check_target,
               'await check_scene_group_policy(db, target, action, {}, protection_only=protection_only)', 'pass')
    elif kind == "wachtrij":
        wijzig(commanding.queue_command,
               'await check_scene_group_policy(db, device, action, params, safe_mode=True)', 'pass')
    elif kind == "worker":
        wijzig(CommandsMixin._precheck,
               'await check_scene_group_policy(db, dev, cmd.action, cmd.params, safe_mode=True)', 'pass')
    elif kind == "simulatie-e8":
        wijzig(lights._dry_run, 'await check_scene_group_policy(db, target, action, params)', 'pass')
    elif kind == "inventaris":
        wijzig(IngestMixin._on_info,
               'st.update(member_identities=extra.get("member_identities"), '
               'members_complete=extra.get("members_complete"))', 'pass')
    elif kind == "adapter-leden":
        wijzig(HueModel.group_state, 'member_identities=members,', 'member_identities=[],')
    elif kind == "adapter-volledigheid":
        wijzig(HueModel.group_members, 'return sorted(members), complete', 'return sorted(members), True')
    elif kind == "leden-woning":
        wijzig(func, 'Device.home_id == group.home_id,', '')
    elif kind == "leden-provider":
        wijzig(func, 'Device.provider == group.provider,', '')
    elif kind == "scene-woning":
        wijzig(func, 'group is not None and group.home_id == device.home_id', 'group is not None')
    elif kind == "scene-soort":
        wijzig(func, 'group.kind != "light_group"', 'False')
    elif kind == "scene-integratie":
        wijzig(func, 'group.integration_id != device.integration_id', 'False')
    elif kind == "scene-verse-groep":
        wijzig(func, 'db.get(Device, group_id, populate_existing=True)', 'db.get(Device, group_id)')
    elif kind == "oude-params":
        wijzig(lights.patch_mapping,
               '(LIGHT_ACTIONS.get(target.kind) or ("recall_scene",))[0]', 'light_command(target, step)[0]')
    elif kind == "gate-uit-te-streng":
        wijzig(func, 'if unknown_members and facts[0].commissioning_gate:', 'if unknown_members:')
    else:
        raise AssertionError("Onbekende mutatie")
'''


if __name__ == "__main__":
    raise SystemExit(harness.main())
