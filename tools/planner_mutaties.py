#!/usr/bin/env python3
"""Mutatiebewijs voor planner, profielherstel en adaptieve activering."""

import handhaving_mutaties as harness

harness.TEST_FILE = "test_inbedrijfstelling_planner.py"
harness.CASES = {
    action: f"test_experiment_vroege_controle_en_adapter[untested-{action}]"
    for action in ("create", "resume", "stop", "delete", "worker", "worker-paused")
}
harness.CASES["adaptief"] = "test_adaptief_passieve_fit_en_bewaakte_activering[untested]"
harness.CASES["adaptief-uit"] = "test_adaptief_uitschakelen_wijzigt_een_actieve_regeling[untested]"
harness.PLUGIN = '''
import inspect
import textwrap
import pytest

@pytest.fixture(autouse=True)
def beschadig(monkeypatch):
    from shc_api import commanding
    from shc_api.routers import optimization
    from shc_api.worker.climate_loop import ClimateMixin

    action = MUTATION
    routes = {
        "create": optimization.create_experiment,
        "resume": optimization.resume_experiment,
        "stop": commanding.experiment_restore_zone,
        "delete": commanding.experiment_restore_zone,
        "worker": ClimateMixin._experiment_step,
        "worker-paused": commanding.experiment_restore_zone,
        "adaptief": optimization.adaptive_activate,
        "adaptief-uit": optimization.adaptive_disable,
    }
    func = routes[action]
    call = 'await enforce_zone_configuration(db, z)'
    if func is ClimateMixin._experiment_step or func is commanding.experiment_restore_zone:
        call = 'await check_zone_configuration(db, zone)'
    source = textwrap.dedent(inspect.getsource(func))
    assert call in source, "De mutatie vond haar controle niet."
    namespace = {}
    exec(compile(source.replace(call, 'pass', 1), "<plannermutatie>", "exec"), func.__globals__, namespace)
    monkeypatch.setattr(func, "__code__", namespace[func.__name__].__code__)
'''


if __name__ == "__main__":
    raise SystemExit(harness.main())
