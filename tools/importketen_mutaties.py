#!/usr/bin/env python3
"""Mutatiebewijs voor herstelvrijgave en geen overerving na een verhuizing."""

import handhaving_mutaties as harness

harness.TEST_FILE = "test_inbedrijfstelling_importketen.py"
harness.CASES = {
    "vrijgave-lock": "test_herstel_beheertransactie_gebruikt_dezelfde_lock_en_behoudt_gate[vrijgave]",
    "archief-lock": "test_herstel_beheertransactie_gebruikt_dezelfde_lock_en_behoudt_gate[archiveren]",
    "gate-na-vrijgave": "test_import_vrijgave_behoudt_gate_tot_adapter[untested-v2-web]",
    "archief-wachtrij": "test_import_vrijgave_behoudt_gate_tot_adapter[new_home-v2-web]",
    "archief-worker": "test_import_vrijgave_behoudt_gate_tot_adapter[new_home-v2-web]",
}
harness.PLUGIN = '''
import inspect
import textwrap
import pytest

@pytest.fixture(autouse=True)
def beschadig(monkeypatch):
    from shc_api import commanding
    from shc_api.routers import system
    from shc_api.worker.commands import CommandsMixin

    if MUTATION in {"vrijgave-lock", "archief-lock"}:
        func = system.release_safe_mode if MUTATION == "vrijgave-lock" else system.archive_home
        old, new = 'await lock_commissioning(db)', 'pass'
    elif MUTATION == "archief-wachtrij":
        func = commanding.queue_command
        old, new = 'await check_active_target(db, home_id, device, climate=action == "set_hvac")', 'pass'
    elif MUTATION == "archief-worker":
        func = CommandsMixin._precheck
        old, new = 'await check_active_target(db, cmd.home_id, dev, climate=cmd.action == "set_hvac")', 'pass'
    else:
        async def vrijgave_zet_gate_uit(db, key, value):
            await original(db, key, value)
            if key == 'safe_mode' and value is False:
                await original(db, 'commissioning_gate', {
                    'version': 1, 'enabled': False, 'origin': 'existing_devices',
                    'enabled_at': None, 'enabled_by': None,
                })
        original = system.set_setting
        monkeypatch.setattr(system, 'set_setting', vrijgave_zet_gate_uit)
        return
    source = textwrap.dedent(inspect.getsource(func))
    assert old in source
    namespace = {}
    exec(compile(source.replace(old, new, 1), "<importmutatie>", "exec"), func.__globals__, namespace)
    monkeypatch.setattr(func, "__code__", namespace[func.__name__].__code__)
'''


if __name__ == "__main__":
    raise SystemExit(harness.main())
