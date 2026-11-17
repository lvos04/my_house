#!/usr/bin/env python3
"""Draai doelgerichte tegenproeven in aparte pytest-processen, zonder bronbestanden te wijzigen.

Elke geselecteerde ingang wordt echt aangeroepen. De tijdelijke plugin verwijdert uitsluitend in
het testproces de genoemde controle. Bij leesroutes introduceert hij opzettelijk een verboden
dispatch: daar bestaat immers geen schakelcontrole om te verwijderen. Alleen een assertionfout
(exitcode 1, geen collection-/setupfout) telt als een gedode mutatie. Geen echte apparaten gebruiken.
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
CASES: dict[str, tuple[str, str]] = {}


def case(name: str, mutation: str, test: str) -> None:
    CASES[name] = mutation, test


case("zone-patch", "zone-patch", "test_boiler_routes.py::test_zone_patch_weigert_verkeerd_of_beschermd_doel")
case("zone-typen", "zone-typen", "test_boiler_routes.py::test_zone_patch_unit_en_sensor_zijn_niet_uitwisselbaar")
case("zone-command", "command", "test_boiler_lights.py::test_zonecommando_bescherming_voor_offline")
case("licht-pad-body", "alles", "test_boiler_lights.py::test_lichtroute_beschermd_pad_en_body")
for method in ("post", "put", "patch"):
    case(
        f"knop-{method}",
        "mapping",
        f"test_boiler_lights.py::test_knopopslag_weigert_beschermd_doel_atomair[zone-{method}]",
    )
for way in ("licht", "scene", "zone", "hervatten", "gecombineerd"):
    case(f"knopdruk-{way}", "command", f"test_boiler_ketens.py::test_echte_knopdruk_en_replay[{way}]")
for way in ("licht", "scene", "zone"):
    case(f"simulatie-{way}", "command", f"test_boiler_ketens.py::test_knopsimulatie_beschermde_oude_koppeling[{way}]")
# De droge hervatsimulatie belooft alleen configuratie; de echte hervatketen staat hierboven.
for way in ("schema", "beleid", "hervatten", "profiel", "profielwijziging", "planner", "adaptief"):
    case(f"automatisering-{way}", "command", f"test_boiler_ketens.py::test_automatisering_tot_aan_adapter[{way}]")
for way in ("pauzeren", "hervatten", "stoppen", "verwijderen"):
    case(f"planner-{way}", "command", f"test_boiler_ketens.py::test_planner_overgangen_met_profielherstel[{way}]")
for source in ("user", "button", "engine"):
    for retry in (False, True):
        case(
            f"worker-{source}-{retry}",
            "worker",
            f"test_boiler_ketens.py::test_worker_hercontrole_na_rolwijziging[{retry}-{source}]",
        )
for actor in ("web-admin", "phone-admin", "phone-control", "phone-viewer", "web-viewer"):
    case(
        f"rechten-{actor}",
        "rechten" if actor.endswith("viewer") else "alles",
        f"test_boiler_ketens.py::test_volledige_rechtenmatrix[{actor}]",
    )
for entrance in ("web-v2", "web-v1", "cli-v2", "cli-v1"):
    case(
        f"import-{entrance}",
        "worker",
        f"test_boiler_ketens.py::test_import_vrijgave_en_oude_knop_tot_adapter[{entrance}]",
    )
for way in ("binden", "ontkoppelen", "vervangen", "archiveren", "getest"):
    case(
        f"levensloop-{way}", "command", f"test_boiler_ketens.py::test_apparaatlevensloop_en_oude_relaiskoppeling[{way}]"
    )
for entrance in ("api", "worker"):
    case(
        f"adapter-{entrance}",
        "adapter",
        f"test_boiler_ketens.py::test_productie_homewizard_hook_blijft_dicht[{entrance}]",
    )
case("worker-actiematrix", "worker", "test_boiler_ketens.py::test_worker_actiematrix_via_interne_commandokern")
case("interne-kern", "command", "test_boiler_ketens.py::test_interne_commandokern_weigert_elke_bron_en_actie")
case("websocket", "websocket", "test_boiler_ketens.py::test_websocket_heeft_geen_commandodispatch")
for entrance in ("discover", "pair", "probe", "health"):
    case(
        f"apparaat-{entrance}",
        f"leesroute-{entrance}",
        "test_boiler_ketens.py::test_ontdekken_koppelen_adres_diagnose_schakelen_nooit",
    )
case("vervangen-naar-boiler", "vervangen", "test_boiler_herstel.py::test_vervangen_naar_boiler_via_api")
case(
    "vervangen-toegestaan", "vervangen-te-streng",
    "test_boiler_herstel.py::test_boiler_zelf_vervangen_blijft_toegestaan",
)
for link in ("unit", "sensor", "knopstap", "knop"):
    case(
        f"vervangen-{link}", "vervangen",
        f"test_boiler_herstel.py::test_vervangen_naar_beschermd_doel_atomair[{link}]",
    )
for reason in ("archief", "licht-in-klimaat", "zone-in-licht"):
    for method in ("post", "put", "patch"):
        case(
            f"knop-{reason}-{method}", "mapping",
            f"test_boiler_herstel.py::test_knopbeleid_voor_archief_en_modus[{reason}-{method}]",
        )
case("simulatie-archief", "droog-beleid", "test_boiler_herstel.py::test_simulatie_beschermd_archief_weigert_beleid")
case("oude-koppeling-uitzetten", "uitzetten", "test_boiler_herstel.py::test_oude_beschermde_koppeling_alleen_uitzetten")
case(
    "oude-koppeling-aanzetten", "uitzetten-te-ruim",
    "test_boiler_herstel.py::test_oude_beschermde_koppeling_alleen_uitzetten",
)
for way in ("h1a", "h1b", "h1c"):
    case(f"roltoekenning-{way}", "verwijzingen",
         f"test_boiler_herstel_2.py::test_roltoekenning_na_historische_vervanging[{way}]")
for role in ("boiler_energy", "eigen_beveiligde_rol"):
    for way in ("binden", "bestaand-doel", "meeverhuizen"):
        for link in ("unit", "sensor", "knopstap", "knop"):
            case(f"h1d-{role}-{way}-{link}", "verwijzingen",
                 f"test_boiler_herstel_2.py::test_h1d_verwijzingen_bij_beschermde_rol[{role}-{way}-{link}]")
for kind, action in (("energy_socket", "toggle"), ("scene", "toggle"), ("light", "recall_scene"),
                     ("light", "set_group"), ("light_group", "set_light")):
    case(f"stapsoort-{kind}-{action}", "stapsoort",
         f"test_boiler_herstel_2.py::test_knopstap_vervangen_vereist_passende_soort[{kind}-{action}]")
for link in ("unit", "sensor"):
    case(f"klimaatsoort-{link}", "vervangen-klimaatsoort",
         f"test_boiler_herstel_2.py::test_klimaatvervanging_vereist_passende_soort[{link}]")
for target in ("licht", "zone"):
    case(f"simulatie-modus-{target}", "simulatie-modus",
         f"test_boiler_herstel_2.py::test_simulatie_verkeerde_modus_eerst_beleid[{target}]")
case("lamp-vervangen-toegestaan", "stapsoort-te-streng",
     "test_boiler_herstel_2.py::test_lamp_door_lamp_vervangen_blijft_werken")

PLUGIN = r"""
import json
import pytest

@pytest.fixture(autouse=True)
def beschadig_controle(monkeypatch):
    from shc_api import commanding, deps
    from shc_api.routers import lights, zones, devices
    from shc_api.worker import commands
    from shc_integrations.homewizard import HomeWizardAdapter
    kind = MUTATION
    if kind == "zone-typen":
        import inspect
        source = inspect.getsource(zones.patch_zone)
        source = source[source.index("async def "):]
        source = source.replace('d.kind != "ac_unit"', 'False')
        source = source.replace('d.kind not in SENSOR_KINDS', 'False')
        functions = {}
        exec(compile(source, "<mutatie-zone-typen>", "exec"), zones.patch_zone.__globals__, functions)
        monkeypatch.setattr(zones.patch_zone, "__code__", functions["patch_zone"].__code__)
    if kind in ("command", "alles"):
        monkeypatch.setattr(commanding, "check_command", lambda *a: None)
        monkeypatch.setattr(lights, "check_command", lambda *a: None)
    if kind == "droog-beleid":
        monkeypatch.setattr(lights, "check_command", lambda *a: None)
    if kind == "vervangen":
        monkeypatch.setattr(devices, "check_command", lambda *a: None)
    if kind == "vervangen-te-streng":
        async def altijd_weigeren(*a):
            raise devices.api_error(409, "policy_denied", "Mutatie weigert ook de toegestane vervanging.")
        monkeypatch.setattr(devices, "_check_reference_policy", altijd_weigeren)
    if kind == "verwijzingen":
        async def zonder_verwijzingscontrole(*a):
            pass
        monkeypatch.setattr(devices, "_check_reference_policy", zonder_verwijzingscontrole)
    if kind in ("stapsoort", "stapsoort-te-streng"):
        def stapsoort(*a):
            if kind == "stapsoort-te-streng":
                raise devices.api_error(409, "role_mismatch", "Mutatie weigert ook een passende lamp.")
        monkeypatch.setattr(devices, "_check_step_kinds", stapsoort)
    if kind in ("simulatie-modus", "vervangen-klimaatsoort"):
        import inspect
        func = lights.simulate if kind == "simulatie-modus" else devices.replace_device
        source = inspect.getsource(func)
        source = source[source.index("async def "):]
        if kind == "simulatie-modus":
            source = source.replace('entry["steps"].append(await _dry_run(db, home, d, step, m.mode))',
                '''if step["type"] != ("zone" if m.mode == "climate" else "light"):
                    entry["steps"].append({"type": step["type"], "result": "invalid"})
                    continue
                entry["steps"].append(await _dry_run(db, home, d, step, m.mode))''')
        else:
            source = source.replace('new.kind != "ac_unit"', 'False').replace('new.kind not in SENSOR_KINDS', 'False')
        functions = {}
        exec(compile(source, "<mutatie-herstel-2>", "exec"), func.__globals__, functions)
        monkeypatch.setattr(func, "__code__", functions[func.__name__].__code__)
    if kind in ("uitzetten", "uitzetten-te-ruim"):
        import inspect
        source = inspect.getsource(lights.patch_mapping)
        source = source[source.index("async def "):]
        source = source.replace('body.model_fields_set == {"enabled"} and body.enabled is False',
                                'False' if kind == "uitzetten" else 'body.enabled is not None')
        functions = {}
        exec(compile(source, "<mutatie-uitzetten>", "exec"), lights.patch_mapping.__globals__, functions)
        monkeypatch.setattr(lights.patch_mapping, "__code__", functions["patch_mapping"].__code__)
    if kind in ("zone-patch", "alles"):
        monkeypatch.setattr(zones, "check_command", lambda *a: None)
    if kind in ("mapping", "licht", "alles"):
        async def toelaten(*a):
            pass
        monkeypatch.setattr(lights, "_check_target", toelaten)
    if kind == "worker":
        monkeypatch.setattr(commands, "check_command", lambda *a: None)
    if kind == "rechten":
        monkeypatch.setattr(deps.Principal, "can_control", property(lambda self: True))
    if kind == "adapter":
        original = HomeWizardAdapter.__init__
        def zonder_productiehook(self, config, credentials=None, **kw):
            kw["relay_allowed"] = lambda identity: True
            original(self, config, credentials, **kw)
        monkeypatch.setattr(HomeWizardAdapter, "__init__", zonder_productiehook)
    if kind == "websocket":
        from starlette.websockets import WebSocket
        from shc_api.db import sessionmaker
        from shc_api.models import CommandRecord, Device
        import uuid
        original = WebSocket.receive_text
        async def met_verboden_dispatch(self):
            msg = await original(self)
            if msg.startswith("{"):
                body = json.loads(msg)
                if body.get("type") == "command":
                    async with sessionmaker()() as db:
                        dev = await db.get(Device, uuid.UUID(body["device_id"]))
                        db.add(CommandRecord(home_id=dev.home_id, device_id=dev.id,
                                             action="set_relay", params={"on": False},
                                             source="user", actor="Mutatieproef"))
                        await db.commit()
            return msg
        monkeypatch.setattr(WebSocket, "receive_text", met_verboden_dispatch)
"""

# Een hook rond de concrete HTTP-aanvraag voorkomt dat de testopstelling de mutatie overschrijft.
READ_PLUGIN = r"""
@pytest.fixture(autouse=True)
def leesroute_dispatch(monkeypatch):
    if not MUTATION.startswith("leesroute-"):
        return
    import httpx
    from shc_api.routers import devices
    original_request = httpx.AsyncClient.request
    entrance = MUTATION.removeprefix("leesroute-")
    async def request(self, *args, **kw):
        providers = devices.providers()
        info = providers.get("homewizard")
        cls = getattr(info, "adapter", None)
        if cls and hasattr(cls, entrance) and not getattr(cls, "_mutatie", False):
            method = getattr(cls, entrance)
            async def verkeerd(self, *args, **kwargs):
                await self.execute(None)
                return await method(self, *args, **kwargs)
            monkeypatch.setattr(cls, entrance, verkeerd)
            monkeypatch.setattr(cls, "_mutatie", True, raising=False)
        return await original_request(self, *args, **kw)
    monkeypatch.setattr(httpx.AsyncClient, "request", request)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weg", choices=sorted(CASES))
    parser.add_argument("--uitvoer", type=Path, required=True)
    args = parser.parse_args()
    args.uitvoer.mkdir(parents=True, exist_ok=True)
    selected = {args.weg: CASES[args.weg]} if args.weg else CASES
    failures = 0
    with tempfile.TemporaryDirectory(prefix="boiler-mutatie-") as temp:
        plugin = Path(temp) / "boiler_mutatie_plugin.py"
        for name, (mutation, test) in selected.items():
            plugin.write_text(f"MUTATION = {mutation!r}\n" + PLUGIN + READ_PLUGIN)
            env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [temp, os.environ.get("PYTHONPATH")])),
                   "PYTHONDONTWRITEBYTECODE": "1"}
            cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   "-p", "boiler_mutatie_plugin", f"tests/{test}"]
            result = subprocess.run(  # noqa: S603 - executable en testnamen komen uitsluitend uit deze vaste tabel.
                cmd, cwd=ROOT / "apps/api", env=env, capture_output=True, text=True, timeout=180
            )
            output = result.stdout + result.stderr
            (args.uitvoer / f"{name}.txt").write_text(output)
            lines = output.strip().splitlines()
            slot = lines[-1] if lines else "geen uitvoer"
            assertion_failure = "AssertionError" in output or "DID NOT RAISE" in output
            if mutation.startswith("leesroute-"):
                assertion_failure = "AssertionError: Leesroutes mogen execute niet aanroepen" in output
            killed = result.returncode == 1 and " failed" in output and "ERROR " not in output and assertion_failure
            failures += not killed
            row = {
                "weg": name,
                "mutatie": mutation,
                "commando": cmd,
                "exitcode": result.returncode,
                "slotregel": slot,
                "gedood": killed,
            }
            with (args.uitvoer / "resultaten.jsonl").open("a") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                f"{name}: exitcode {result.returncode}; {slot}; " + ("mutatie gedood" if killed else "NIET BEWEZEN"),
                flush=True,
            )
    print(f"{len(selected)} mutaties gedraaid; {failures} niet bewezen")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
