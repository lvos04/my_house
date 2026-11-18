#!/usr/bin/env python3
"""Verwijder iedere geleverde beleidscontrole afzonderlijk in een eigen testproces.

De bronwerkboom blijft intact. Alleen assertionfouten in de bedoelde proef tellen
als gedode mutatie; een import-, opzet- of tijdslimietfout is geen bewijs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = "test_inbedrijfstelling_handhaving.py"
CASES = {
    "policy": "test_direct_weigert_voor_validatie_en_schrijft_niets",
    "bescherming": "test_bescherming_heeft_voorrang",
    "feiten-status": "test_direct_weigert_voor_validatie_en_schrijft_niets",
    "feiten-gate": "test_direct_weigert_voor_validatie_en_schrijft_niets",
    "gate-uit-te-streng": "test_direct_positief_tot_adapter",
    "direct": "test_direct_weigert_voor_validatie_en_schrijft_niets",
    "wachtrij": "test_commandokern_weigert_iedere_bron",
    "claim": "test_claim_weigert_al_voor_sending",
    "uitvoering": "test_worker_hercontrole_na_claim",
    "licht": "test_direct_lichtdoel_en_scene",
    "lock-feiten": "test_interne_ingangen_nemen_eerst_dezelfde_lock",
    "lock-buitenunit": "test_interne_ingangen_nemen_eerst_dezelfde_lock",
    "lock-inventaris": "test_interne_ingangen_nemen_eerst_dezelfde_lock",
    "lock-koppeling": "test_interne_ingangen_nemen_eerst_dezelfde_lock",
}
for route in ("patch_device", "bind", "unbind", "archive_device", "replace_device", "mark_tested",
              "pair", "patch_integration", "remove_integration", "zigbee_setup"):
    CASES[f"lock-{route}"] = "test_api_schrijfwegen_wachten_op_handhaving"

PLUGIN = '''
import inspect
import pytest

@pytest.fixture(autouse=True)
def beschadig(monkeypatch):
    from shc_api import policy, commissioning, commanding
    from shc_api.routers import devices, lights
    from shc_api.worker.commands import CommandsMixin
    kind = MUTATION

    def wijzig(func, old, new):
        source = inspect.getsource(func)
        source = source[source.index("async def " if inspect.iscoroutinefunction(func) else "def "):]
        assert old in source, "De mutatie vond haar controle niet."
        changed = source.replace(old, new, 1)
        namespace = {}
        exec(compile(changed, "<handhavingsmutatie>", "exec"), func.__globals__, namespace)
        monkeypatch.setattr(func, "__code__", namespace[func.__name__].__code__)

    if kind == "policy":
        wijzig(policy.check_command, 'if device.commissioning_gate and', 'if False and')
    elif kind == "bescherming":
        wijzig(policy.check_command, 'if protected:', 'if False:')
    elif kind == "feiten-status":
        wijzig(commissioning.command_facts, 'device.compatibility,', '"tested",')
    elif kind == "feiten-gate":
        wijzig(commissioning.command_facts, '(await read_gate(db))["enabled"]', 'False')
    elif kind == "gate-uit-te-streng":
        wijzig(commissioning.command_facts, '(await read_gate(db))["enabled"]', 'True')
    elif kind in ("direct", "wachtrij", "licht"):
        func = {"direct": commanding.request_zone_command, "wachtrij": commanding.queue_command,
                "licht": lights._check_target}[kind]
        wijzig(func, 'check_command(', '(lambda *args: None)(')
    elif kind in ("claim", "uitvoering"):
        original = CommandsMixin._precheck
        async def zonder(self, db, cmd, *args):
            if cmd.status == ("queued" if kind == "claim" else "sending"):
                return None
            return await original(self, db, cmd, *args)
        monkeypatch.setattr(CommandsMixin, "_precheck", zonder)
    elif kind in ("lock-feiten", "lock-buitenunit", "lock-inventaris", "lock-koppeling"):
        from shc_api.worker.ingest import IngestMixin
        func = {"lock-feiten": commissioning.command_facts, "lock-buitenunit": commanding.lock_outdoor_group,
                "lock-inventaris": IngestMixin.handle_events, "lock-koppeling": lights.normalize_mapping}[kind]
        if kind == "lock-inventaris":
            import textwrap
            source = textwrap.dedent(inspect.getsource(func))
            old = '        await lock_commissioning(db)'
            assert old in source
            namespace = {}
            exec(compile(source.replace(old, '', 1), "<inventarismutatie>", "exec"), func.__globals__, namespace)
            monkeypatch.setattr(func, "__code__", namespace[func.__name__].__code__)
        else:
            wijzig(func, '    await lock_commissioning(db)\\n', '')
    elif kind.startswith("lock-"):
        wijzig(getattr(devices, kind[5:]), '    await lock_commissioning(db)\\n', '')
    else:
        raise AssertionError("Onbekende mutatie")
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutatie", choices=CASES)
    parser.add_argument("--uitvoer", type=Path, required=True)
    args = parser.parse_args()
    args.uitvoer.mkdir(parents=True, exist_ok=True)
    selected = {args.mutatie: CASES[args.mutatie]} if args.mutatie else CASES
    failed = 0
    with (args.uitvoer / "resultaten.jsonl").open("w") as rows:
        for name, test in selected.items():
            with tempfile.TemporaryDirectory(prefix="handhaving-", dir=args.uitvoer) as temp:
                plugin = Path(temp) / "handhaving_mutatie_plugin.py"
                plugin.write_text(PLUGIN.replace("MUTATION", repr(name)))
                env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                       "PYTHONPATH": temp + os.pathsep + os.environ.get("PYTHONPATH", "")}
                cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
                       "-p", "handhaving_mutatie_plugin", f"tests/{TEST_FILE}::{test}"]
                try:
                    result = subprocess.run(cmd, cwd=ROOT / "apps/api", env=env, capture_output=True,  # noqa: S603 — vaste proefnamen.
                                            text=True, timeout=180, check=False)
                    output = result.stdout + result.stderr
                    killed = (result.returncode == 1
                              and ("AssertionError" in output or "Failed: DID NOT RAISE" in output)
                              and f"FAILED tests/{TEST_FILE}::" in output
                              and "ERROR " not in output)
                    code = result.returncode
                except subprocess.TimeoutExpired as exc:
                    output, killed, code = str(exc), False, 124
                (args.uitvoer / f"{name}.log").write_text(output)
                last = output.rstrip().splitlines()
                row = {"mutatie": name, "proef": test, "commando": cmd, "exitcode": code,
                       "slotregel": last[-1] if last else "geen uitvoer", "gedood": killed}
                rows.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.flush()
                failed += not killed
                print(f"{name}: {'gedood' if killed else 'NIET BEWEZEN'}", flush=True)
    print(f"{len(selected)} mutaties gedraaid; {failed} niet bewezen")
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
