#!/usr/bin/env python3
"""Bewijs behoudcontroles met mutaties in aparte testprocessen, zonder bronwijziging.

Dit gereedschap toetst uitsluitend de migratie en gatetoestand van S-P3-710a.
Het bewijst geen bedieningsgate of beheer-API. Alleen tijdelijke proefdatabases
van de bestaande tests worden gebruikt; geen echte apparaten of accounts.
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
UPGRADE_TEST = "tests/test_schema_nieuwer.py::test_ouder_schema_migreert_met_dump"
RESTORE_TEST = (
    "tests/test_inbedrijfstelling_herstel.py::"
    "test_herstel_gate_en_buiten_settings_exact_de_backup[True-False-True-before]"
)
CASES = {
    "bestaande-setting-wijzigen": UPGRADE_TEST,
    "andere-setting-toevoegen": UPGRADE_TEST,
    "andere-tabel-wijzigen": UPGRADE_TEST,
    "gate-verkeerd-initialiseren": UPGRADE_TEST,
    "eerder-aan-vergeten": RESTORE_TEST,
    "herstel-audit-toevoegen": RESTORE_TEST,
    "onbekende-gate-open": "tests/test_inbedrijfstelling_herstel.py::test_onbekende_gate_is_dicht[None]",
}

PLUGIN = r'''
import asyncio
import os
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if (report.when == 'call' and report.failed and call.excinfo is not None
            and call.excinfo.errisinstance(AssertionError)
            and str(call.excinfo.traceback[-1].path).endswith('/' + item.path.name)):
        with open(os.environ['SHC_MUTATIE_ASSERTIONS'], 'a') as file:
            file.write(item.nodeid + '\n')

@pytest.fixture(autouse=True)
def beschadig_controle(monkeypatch):
    from shc_api import backup, cli, commissioning
    if MUTATION in {
        "bestaande-setting-wijzigen", "andere-setting-toevoegen",
        "andere-tabel-wijzigen", "gate-verkeerd-initialiseren",
    }:
        original = cli.migrate
        statements = {
            "bestaande-setting-wijzigen":
                "UPDATE settings SET value='{}'::jsonb WHERE key='proef_s_p3_700'",
            "andere-setting-toevoegen":
                "INSERT INTO settings (key,value) VALUES ('onbedoelde_mutatie','{}'::jsonb)",
            "andere-tabel-wijzigen": "UPDATE users SET username='veranderde_proefbewoner'",
            "gate-verkeerd-initialiseren":
                "UPDATE settings SET value=jsonb_set(value,'{v,enabled}','false') "
                "WHERE key='commissioning_gate'",
        }
        def migrate(url=None, **kw):
            result = original(url, **kw)
            assert url and '/shc_tmp_' in url, "Alleen de eigen tijdelijke proefdatabase is toegestaan"
            async def change():
                engine = create_async_engine(url, poolclass=NullPool)
                try:
                    async with engine.begin() as conn:
                        changed = await conn.execute(text(statements[MUTATION]))
                        assert changed.rowcount == 1, "Mutatie heeft de gevulde proefrij niet bereikt"
                finally:
                    await engine.dispose()
            cli._in_thread(lambda: asyncio.run(change()))
            return result
        monkeypatch.setattr(cli, 'migrate', migrate)
    elif MUTATION == "eerder-aan-vergeten":
        original = commissioning.restored_gate
        def without_before(prior, loaded, mode, at):
            return original(None, loaded, mode, at)
        monkeypatch.setattr(commissioning, 'restored_gate', without_before)
    elif MUTATION == "herstel-audit-toevoegen":
        original = backup._after_restore
        def extra_audit(conn, *args):
            original(conn, *args)
            conn.execute(text("INSERT INTO audit_log (at,actor,action,target,detail) "
                              "VALUES (now(),'proef','onbedoelde_herstelaudit',NULL,'{}')"))
        monkeypatch.setattr(backup, '_after_restore', extra_audit)
    elif MUTATION == "onbekende-gate-open":
        def open_gate(value):
            return {"enabled": False, "known": False}
        monkeypatch.setattr(commissioning.gate_state, '__code__', open_gate.__code__)
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uitvoer", required=True, type=Path)
    parser.add_argument("--mutatie", choices=sorted(CASES))
    args = parser.parse_args()
    args.uitvoer.mkdir(parents=True, exist_ok=True)
    failures = 0
    selected = {args.mutatie: CASES[args.mutatie]} if args.mutatie else CASES
    with tempfile.TemporaryDirectory(prefix="inbedrijfstelling-mutaties-") as temp:
        plugin = Path(temp) / "inbedrijfstelling_mutatie_plugin.py"
        for name, test in selected.items():
            plugin.write_text(f"MUTATION = {name!r}\n" + PLUGIN)
            assertions = args.uitvoer / f"{name}.assertions.txt"
            assertions.unlink(missing_ok=True)
            env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [temp, os.getenv("PYTHONPATH")])),
                   "PYTHONDONTWRITEBYTECODE": "1", "SHC_MUTATIE_ASSERTIONS": str(assertions)}
            cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-o", "faulthandler_timeout=60",
                   "-p", "no:cacheprovider", "-p", "inbedrijfstelling_mutatie_plugin", test]
            try:
                result = subprocess.run(  # noqa: S603 - eigen vaste testnamen; geen shell
                    cmd, cwd=ROOT / "apps/api", env=env, capture_output=True, text=True, timeout=180,
                )
            except subprocess.TimeoutExpired as exc:
                output = (exc.stdout or b"") + (exc.stderr or b"")
                (args.uitvoer / f"{name}.txt").write_bytes(output)
                print(f"{name}: tijdslimiet na 180 seconden; NIET BEWEZEN", flush=True)
                failures += 1
                continue
            output = result.stdout + result.stderr
            (args.uitvoer / f"{name}.txt").write_text(output)
            killed = (result.returncode == 1 and assertions.exists()
                      and assertions.read_text().splitlines() == [test]
                      and "1 failed" in output and "ERROR " not in output)
            failures += not killed
            lines = output.rstrip().splitlines()
            row = {"mutatie": name, "commando": cmd, "exitcode": result.returncode,
                   "slotregel": lines[-1] if lines else "geen uitvoer", "gedood": killed}
            with (args.uitvoer / "resultaten.jsonl").open("a") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{name}: exitcode {result.returncode}; {row['slotregel']}; "
                  + ("mutatie gedood" if killed else "NIET BEWEZEN"), flush=True)
    print(f"{len(selected)} mutaties gedraaid; {failures} niet bewezen")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
