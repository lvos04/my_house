#!/usr/bin/env python3
"""Mutatiebewijs voor de controle van alle doelen van een knopdruk of opslag."""

import handhaving_mutaties as harness

harness.TEST_FILE = "test_inbedrijfstelling_knoppen.py"
harness.CASES = {
    "opslag-post": "test_knopopslag_meerdere_doelen[protected-post]",
    "opslag-put": "test_knopopslag_meerdere_doelen[protected-put]",
    "opslag-patch": "test_knopopslag_meerdere_doelen[protected-patch]",
    "simulatie": "test_meerstapsknop_simulatie_event_en_replay[een-koppeling-untested-hervatten]",
    "fysieke-knop": "test_meerstapsknop_simulatie_event_en_replay[een-koppeling-untested-bedienen]",
    "hervatten": "test_meerstapsknop_simulatie_event_en_replay[een-koppeling-untested-hervatten]",
    "beschermingsvoorrang": "test_knopopslag_meerdere_doelen[protected-put]",
    "gate-uit": "test_meerstapsknop_simulatie_event_en_replay[een-koppeling-gate_uit-hervatten]",
}
harness.PLUGIN = '''
import inspect
import textwrap
import pytest

@pytest.fixture(autouse=True)
def beschadig(monkeypatch):
    from shc_api import commanding
    from shc_api.routers import lights
    from shc_api.worker.ingest import IngestMixin
    kind = MUTATION

    def wijzig(func, old, new, extra=None):
        source = textwrap.dedent(inspect.getsource(func))
        assert old in source, "De mutatie vond haar controle niet."
        source = source.replace(old, new, 1)
        if extra:
            old, new = extra
            assert old in source, "De mutatie vond haar controle niet."
            source = source.replace(old, new, 1)
        namespace = {}
        exec(compile(source, "<knopmutatie>", "exec"), func.__globals__, namespace)
        monkeypatch.setattr(func, "__code__", namespace[func.__name__].__code__)

    if kind in {"opslag-post", "opslag-patch"}:
        wijzig(lights._save_one, 'await check_button_targets(db, d.home_id, mapping_steps(data["action"]))', 'pass')
    elif kind == "opslag-put":
        wijzig(lights.put_mappings,
               'await check_button_targets(db, d.home_id, '
               '(step for m in as_dicts for step in mapping_steps(m["action"])))',
               'pass')
    elif kind == "simulatie":
        async def zonder(*args, **kwargs):
            pass
        monkeypatch.setattr(lights, "check_button_targets", zonder)
    elif kind == "fysieke-knop":
        wijzig(IngestMixin.on_button, 'await check_button_targets(db, dev.home_id, steps, safe_mode=True)', 'pass')
    elif kind == "hervatten":
        wijzig(commanding.check_button_targets, 'if zone_step:', 'if zone_step and step.get("action") != "resume":')
    elif kind == "beschermingsvoorrang":
        wijzig(commanding.check_button_targets, '(True, False):', '(False,):')
    elif kind == "gate-uit":
        wijzig(commanding.check_button_targets, 'if not (await read_gate(db))["enabled"]:', 'if False:',
               ('if protection_only else facts', 'if protection_only else replace(facts, commissioning_gate=True)'))
    else:
        raise AssertionError("Onbekende mutatie")
'''


if __name__ == "__main__":
    raise SystemExit(harness.main())
