"""Herstel een versleutelde bronexport via de webapp in een tweede lokale Custom App."""

import hashlib
import http.client
import json
import secrets
import sys
import tempfile
import traceback
from contextlib import ExitStack
from http.cookies import SimpleCookie
from pathlib import Path

from allinone_container import PYTHON, Trial, docker, within

METER = Path(__file__).with_name("herstel_proefmeter.py")
TESTLOG = Path(__file__).resolve().parents[2] / "docs" / "testlog.md"
BEGIN, END = "<!-- S-P3-735 begin -->", "<!-- S-P3-735 einde -->"
RESTORE = "/system/restore"
EXPECTED = {"users": 2, "homes": 1, "climate_zones": 2, "devices": 1, "device_bindings": 1, "sensor_readings": 4}
READINGS = """SELECT count(*) FILTER (WHERE event_id='sql-null' AND raw IS NULL),
                     count(*) FILTER (WHERE event_id='json-null' AND raw='null'::jsonb),
                     count(*) FILTER (WHERE event_id='veld-ontbreekt' AND raw='{}'::jsonb),
                     count(*) FILTER (WHERE event_id='veld-nul' AND raw='{"v":0}'::jsonb)
              FROM sensor_readings"""


class Proeffout(AssertionError):
    """Een foutmelding die zonder proefgegevens in het testlog mag."""


class WebApp:
    """Een eigen browsersessie per installatie, rechtstreeks naar nginx op het interne proefnet."""

    def __init__(self, address):
        self.address = address
        self.cookies = SimpleCookie()
        self.csrf = ""

    def request(self, method, path, data=None, *, archive=None, phrase=None, expected=200):
        headers = {"Cookie": "; ".join(f"{key}={c.value}" for key, c in self.cookies.items()),
                   "X-CSRF-Token": self.csrf, "Accept": "application/json"}
        body = None
        if archive is not None:
            headers.update({"Content-Type": "application/octet-stream", "X-Backup-Passphrase": phrase})
            body = archive
        elif data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        conn = http.client.HTTPConnection(self.address, 8080, timeout=90)
        try:
            conn.request(method, "/api/v1" + path, body=body, headers=headers)
            res = conn.getresponse()
            for key, value in res.getheaders():
                if key.lower() == "set-cookie":
                    self.cookies.load(value)
            payload = res.read()
            if "application/json" in res.getheader("Content-Type", ""):
                payload = json.loads(payload)
            if res.status != expected:
                # Toon uitsluitend de routegroep en de foutcode. Een taak- of bestands-ID kan toegang geven.
                detail = payload.get("detail") if isinstance(payload, dict) else None
                code = detail.get("code") if isinstance(detail, dict) else "onverwacht antwoord"
                raise Proeffout(f"{method} {path.split('/')[1]} gaf HTTP {res.status} ({code}), verwacht {expected}")
            return payload
        finally:
            conn.close()

    def authenticate(self, action, account):
        session = self.request("POST", "/auth/" + action, account)
        assert session["user"]["username"] == account["username"] and session["csrf_token"]
        self.csrf = session["csrf_token"]
        return session["user"]


def snapshot(trial, name, *, live_target=False):
    # De gesloten bron wordt integraal vergeleken. Op een leeg, draaiend doel ververst de worker
    # zijn vier klok- en bewakingsinstellingen. De overige rijen tellen ook bij de weigering mee.
    condition = """CASE WHEN schemaname='public' AND tablename='settings' THEN
        'WHERE key NOT IN (''worker_heartbeat'',''worker_loops'',''clock_check'',''status_watch'')'
        ELSE '' END""" if live_target else "''"
    query = r"""
        SELECT format($q$SELECT %L, count(*), encode(sha256(convert_to(coalesce(string_agg(h, '' ORDER BY h),
            ''), 'UTF8')), 'hex') FROM (SELECT encode(sha256(record_send(r)), 'hex') AS h FROM %I.%I r %s) rows$q$,
            schemaname || '.' || tablename, schemaname, tablename, CONDITION)
        FROM pg_tables WHERE schemaname <> 'information_schema' AND schemaname !~ '^pg_' ORDER BY schemaname, tablename
        \gexec
    """
    result = {}
    for line in trial.sql(name, query.replace("CONDITION", condition)).decode().splitlines():
        table, count, digest = line.split("|")
        result[table] = (int(count), digest)
    assert set("public." + name for name in EXPECTED) <= result.keys()
    return result


def simulate(trial, name, side, values):
    trial.exec(name, *PYTHON, "-c", METER.read_text(), side, content=json.dumps(values).encode())


def archive_inventory(trial, name, upload, phrase):
    # De inventaris in de versleutelde bronkopie telt de duurzame rijen. Het totaal van
    # de archiefbestanden in upload["tables"] omvat ook installatielokale instellingen.
    read_counts = """
import json, sys
from pathlib import Path
from uuid import UUID
from shc_api.backup import verify_file
path = Path("/appdata/backups/uploads") / (UUID(sys.argv[1]).hex + ".shcbak")
proof = verify_file(path, sys.stdin.read(), collect=False).manifest["source_pause"]["inventory"]
print(json.dumps({table: entry["rows"] for table, entry in proof.items()}))
"""
    measured = trial.exec(name, *PYTHON, "-c", read_counts, upload["upload_id"], content=phrase.encode())
    counts = json.loads(measured.stdout)
    assert counts.keys() == upload["tables"].keys()
    return counts


def check_inventory(result, tables):
    inventory = result["inventory"]
    assert result["restored_at"]
    if inventory.keys() != tables.keys():
        raise Proeffout("Tabellen verschillen tussen archief en vergelijking: "
                        + ", ".join(sorted(inventory.keys() ^ tables.keys())))
    different = [f"{t} (archief {tables[t]}, bron {row['source']}, doel {row['target']})"
                 for t, row in sorted(inventory.items())
                 if row["same"] is not True or any(type(row[k]) is not int or row[k] != tables[t]
                                                  for k in ("source", "target"))]
    if different:
        raise Proeffout(f"Inhoudsverschil in {', '.join(different)}")
    assert all(inventory[t] == {"source": n, "target": n, "same": True} for t, n in EXPECTED.items())


def prepare_restore(web, request):
    preview = web.request("POST", RESTORE + "/preview", request)
    comparison = preview["comparison"]
    confirmed = dict(request, rollback_id=comparison["rollback_id"], confirmation=preview["confirmation"])
    return confirmed, comparison


def apply_and_read_report(web, body, tables):
    task = web.request("POST", RESTORE + "/apply", body, expected=202)
    code = task["job_id"]
    path = RESTORE + "/jobs/" + code
    assert code and task["poll"] == "/api/v1" + path
    reader = WebApp(web.address)  # De oude sessie vervalt tijdens het herstel.

    def finished():
        job = reader.request("GET", path)
        assert job["job_id"] == code and job["state"] in {"running", "done"}, "Onverwachte hersteltaak"
        if job["state"] == "running":
            return False
        assert job["report"]["mode"] == body["mode"] and job["report"]["counts"] == tables
        return True

    within(finished, "Geen volledig taakrapport binnen de proeftijd")
    return code


def installation(trial, label, network):
    volume = trial.storage(label)
    name = trial.container(label, volume, network=network, options=("--pull=never", "--cpus", "1", "--memory", "640m"))
    trial.healthy(name)
    info = json.loads(docker("inspect", name).stdout)[0]
    mount, = (m for m in info["Mounts"] if m["Destination"] == "/appdata")
    assert mount["Name"] == volume
    address = info["NetworkSettings"]["Networks"][network]["IPAddress"]
    assert address
    return name, WebApp(address)


def measure(trial, record):
    trial.image = docker("image", "inspect", "--format", "{{.Id}}", trial.image).stdout.decode().strip()
    network = trial.prefix
    docker("network", "create", "--internal", network)
    trial.cleanup.callback(docker, "network", "rm", network)
    a, source = installation(trial, "bron", network)
    b, target = installation(trial, "doel", network)
    assert source.address != target.address
    record("Start", "A en B gezond op afzonderlijke volumes en verschillende interne adressen.")

    admin = {"username": "bronbeheer", "password": secrets.token_urlsafe(32)}
    viewer = {"username": "proefkijker", "password": secrets.token_urlsafe(32)}
    source.authenticate("setup", admin)
    source.request("POST", "/users", {**viewer, "role": "viewer"})
    home = source.request("POST", "/homes", {"name": "Proefwoning"})
    source_viewer = WebApp(source.address)
    assert source_viewer.authenticate("login", viewer)["role"] == "viewer"
    assert source_viewer.request("GET", "/homes") == [home]
    home_path = "/homes/" + home["id"]
    zones = {z["id"] for z in source.request("GET", home_path + "/zones")}
    values = {key: secrets.token_urlsafe(32) for key in ("meter", "backup")}
    source.request("PUT", "/system/backup-settings", {"enabled": False, "passphrase": values["backup"]})
    simulate(trial, a, "bron", values)
    device, = source.request("GET", home_path + "/devices")
    assert device["stable_identity"] == "proefmeter-735"
    source.request("POST", "/devices/" + device["id"] + "/bind", {"role": "grid"})

    target_admin = {"username": "doelbeheer", "password": secrets.token_urlsafe(32)}
    target.authenticate("setup", target_admin)
    phrase = secrets.token_urlsafe(32)
    source.request("POST", RESTORE + "/source/pause")
    export = source.request("POST", RESTORE + "/source/export", {"passphrase": phrase, "stop_source": True})
    archive = source.request("GET", RESTORE + "/files/" + export["file_id"])
    assert archive.startswith(b"SHCBAK2\n")
    before = snapshot(trial, a)
    assert all(before["public." + table][0] == count for table, count in EXPECTED.items())
    assert trial.sql(a, READINGS).strip() == b"1|1|1|1"
    record("Bronexport", "2 gebruikers, 1 woning, 2 zones, 1 meter, 1 rolbinding en 4 metingen in A.")

    empty = snapshot(trial, b, live_target=True)
    assert empty["public.users"][0] == 1
    assert all(empty["public." + table][0] == 0 for table in EXPECTED if table != "users")
    wrong = secrets.token_urlsafe(32)
    refused = target.request("POST", RESTORE + "/verify", archive=archive, phrase=wrong, expected=422)
    assert refused["detail"]["code"] == "backup_invalid"
    assert snapshot(trial, b, live_target=True) == empty
    trial.exec(b, *PYTHON, "-c", "from pathlib import Path; assert not any(Path('/appdata/backups/uploads').glob('*'))")
    record("Verkeerde wachtwoordzin", "HTTP 422 met backup_invalid. Alleen de tijdelijke beheerder staat op B.")

    damaged = archive[:-1] + bytes([archive[-1] ^ 1])
    refused = target.request("POST", RESTORE + "/verify", archive=damaged, phrase=phrase, expected=422)
    assert refused["detail"]["code"] == "backup_invalid"
    assert snapshot(trial, b, live_target=True) == empty
    record("Beschadigde bundel", "Bij de juiste wachtwoordzin gaf de beschadigde export HTTP 422. "
           "Alle gemeten doelrijen bleven gelijk.")

    upload = target.request("POST", RESTORE + "/verify", archive=archive, phrase=phrase)
    assert upload["restorable"] and upload["secrets_included"] and not upload["redacted"]
    assert all(upload["tables"][t] == n for t, n in EXPECTED.items())
    expected_inventory = archive_inventory(trial, b, upload, phrase)
    body = {"upload_id": upload["upload_id"], "passphrase": phrase}
    drill = target.request("POST", RESTORE + "/drill", body)
    assert drill["data_ok"] and drill["drill_id"], drill["migration_refusal"]
    assert drill["source_id"] != drill["target_id"]
    body, comp = prepare_restore(target, dict(body, mode="migration", drill_id=drill["drill_id"]))
    assert comp["archive_sha256"] == hashlib.sha256(archive).hexdigest()
    rollback = target.request("GET", RESTORE + "/files/" + comp["rollback_id"])
    assert comp["rollback_sha256"] == hashlib.sha256(rollback).hexdigest()
    assert all(comp["tables"][t]["before"] == empty["public." + t][0]
               and comp["tables"][t]["after"] == n for t, n in EXPECTED.items())
    reviewed = snapshot(trial, b, live_target=True)
    refused = target.request("POST", RESTORE + "/apply", {**body, "passphrase": wrong}, expected=422)
    assert refused["detail"]["code"] == "backup_invalid"
    assert snapshot(trial, b, live_target=True) == reviewed
    record("Verkeerde wachtwoordzin bij toepassen", "De bevestigde vergelijking leidde tot HTTP 422. B bleef leeg.")
    restore_code = apply_and_read_report(target, body, upload["tables"])
    record("Herstel", "Archief en doelback-up op SHA-256 gecontroleerd. De bevestigde webtaak bereikte done.")

    docker("restart", b)
    trial.healthy(b)
    target = WebApp(target.address)
    assert target.authenticate("login", admin)["role"] == "admin"
    assert WebApp(target.address).authenticate("login", viewer)["role"] == "viewer"
    # Herstel mag de meter niet zelf als opnieuw waargenomen aanmerken. Pas na controle van de
    # herstelde identiteit en bronhistorie levert de simulator een nieuwe waarneming aan de worker.
    first = target.request("GET", RESTORE + "/compare")
    assert not first["migration"]["confirmation"]
    assert all(first["inventory"][t]["same"] is False for t in ("integrations", "settings"))
    read_key = ("from pathlib import Path; from shc_allinone.supervisor import service_env; "
                "import sys; sys.stdout.buffer.write(Path(service_env()['SHC_SECRET_KEY_FILE']).read_bytes())")
    old_key = trial.exec(a, *PYTHON, "-c", read_key).stdout
    counts = target.request("POST", RESTORE + "/rekey", archive=old_key, phrase="")
    assert counts["rekeyed"] == 1 and counts["failed"] == counts["settings_failed"] == 0
    first = target.request("GET", RESTORE + "/compare")
    check_inventory(first, expected_inventory)
    assert target.request("GET", "/homes") == [home]
    assert {z["id"] for z in target.request("GET", home_path + "/zones")} == zones
    assert first["missing_devices"] == 0 and [d["id"] for d in first["devices"]] == [device["id"]]
    assert first["devices"][0]["status"] == "unavailable" and first["migration"]["confirmation"] is None
    restored = snapshot(trial, b)
    assert trial.sql(b, READINGS).strip() == b"1|1|1|1"
    for table in ("users", "homes", "rooms", "logical_roles", "device_bindings", "sensor_readings"):
        assert restored["public." + table] == before["public." + table], f"Bronrijen verschillen in {table}"
    simulate(trial, b, "doel", values)
    record("Sleutelomzetting", "Vóór omzetting kon B de gevulde koppeling en back-upinstelling niet bevestigen. "
           "Na omzetting las de proefmeter beide oorspronkelijke waarden met de doelsleutel.")
    result = target.request("GET", RESTORE + "/compare")
    check_inventory(result, expected_inventory)
    assert result["source_homes"] == result["homes"] == {home["id"]: home["name"]}
    assert result["missing_devices"] == 0 and not result["duplicates"]
    assert result["functions_verified"] and result["summary"] == {"compatible": 1, "partial": 0, "unavailable": 0}
    assert result["devices"][0]["stable_identity"] == device["stable_identity"]
    assert result["functions"][0]["device_id"] == device["id"] and result["functions"][0]["key"] == "role:grid"
    assert result["safe_mode"] and result["migration"]["source_complete"] and result["migration"]["confirmation"]
    record("Herstart B", f"Beide bronaccounts ingelogd. restore/compare bevestigde de broninhoud in alle "
           f"{len(result['inventory'])} overdraagbare tabellen, ook na een nieuwe doelmeting. "
           "De vier bronmetingen zijn binair gelijk.")

    assert before == snapshot(trial, a), "De database op A is tijdens het herstel in B gewijzigd"
    record("Bron na herstel", f"Alle {len(before)} brontabellen hebben hetzelfde aantal en dezelfde binaire rijinhoud.")

    back = target.request("POST", RESTORE + "/verify", archive=rollback, phrase=phrase)
    request, comparison = prepare_restore(target, {"upload_id": back["upload_id"], "passphrase": phrase,
                                                   "mode": "same_home"})
    assert comparison["archive_sha256"] == comp["rollback_sha256"]
    rollback_code = apply_and_read_report(target, request, back["tables"])
    assert rollback_code != restore_code
    original = WebApp(target.address)
    original.request("GET", RESTORE + "/jobs/" + restore_code[:-1], expected=404)
    assert original.authenticate("login", target_admin)["role"] == "admin" and original.request("GET", "/homes") == []
    assert snapshot(trial, b)["public.users"] == empty["public.users"]
    assert before == snapshot(trial, a), "De database op A is tijdens de terugval in B gewijzigd"
    record("Terugval", "De geverifieerde doelback-up is teruggezet. De oorspronkelijke doelbeheerder kan inloggen "
           "en B bevat de oorspronkelijke gebruikersrij en geen bronwoning. De brontabellen op A bleven gelijk.")

    for name in (a, b):
        logs = trial.logs(name)
        if not logs:
            raise Proeffout("Containerlog ontbreekt bij de controle van de taakcodes")
        for code in (restore_code, rollback_code):
            if len(code) < 8 or any(code[i:i + 8].encode() in logs for i in range(len(code) - 7)):
                raise Proeffout("Hersteltaakcode of deel ervan leesbaar in de containerlogs")
        if name == b:
            entry = b'GET /api/v1/system/restore/jobs/{job_id} HTTP/1.1" '
            if logs.count(entry + b"200") < 2 or entry + b"404" not in logs:
                raise Proeffout("Afgeschermde toegangsregels voor de taakrapporten ontbreken")
    record("Taakrapporten", "Herstel en terugval gaven zonder sessie het juiste rapport met de archieftellingen. "
           "Een ingekorte code kreeg HTTP 404. Beide containerlogs bevatten nul reeksen van acht opeenvolgende "
           "tekens uit de twee taakcodes.")


def check_restore_stages(rows):
    required = {"Beschadigde bundel", "Sleutelomzetting", "Terugval", "Taakrapporten"}
    missing = required.difference(stage for stage, _ in rows)
    if missing:
        raise Proeffout("Herstelbewijs mist de volgende proeven: " + ", ".join(sorted(missing)))


def write_report(rows, outcome):
    original = TESTLOG.read_text()
    header = "## S-P3-735 — back-up A naar herstel B"
    table = ["| Controle | Meting |", "| --- | --- |", *(f"| {k} | {v} |" for k, v in rows)]
    section = "\n".join([BEGIN, header, "", outcome, "", *table, END])
    if BEGIN in original:
        head, _, old = original.partition(BEGIN)
        _, end, tail = old.partition(END)
        assert end, "De eindmarkering van de herstelproef ontbreekt in het testlog."
        updated = head + section + tail
    else:
        updated = original.rstrip() + "\n\n" + section + "\n"
    with tempfile.NamedTemporaryFile(dir=TESTLOG.parent, mode="w", encoding="utf-8", delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(updated)
            output.flush()
            temporary.chmod(TESTLOG.stat().st_mode)
            temporary.replace(TESTLOG)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    rows = []
    pending = "Proef gestart. De eindvergelijking is nog niet voltooid."
    write_report(rows, pending)

    def record(stage, summary):
        rows.append((stage, summary))
        write_report(rows, pending)
        print(f"{stage}. {summary}", flush=True)

    try:
        with ExitStack() as cleanup:
            measure(Trial(sys.argv[1], cleanup, step="735"), record)
        check_restore_stages(rows)
    except BaseException as exc:
        location = traceback.extract_tb(exc.__traceback__)[-1].name
        reason = str(exc) if isinstance(exc, Proeffout) else f"{type(exc).__name__} in {location}"
        write_report(rows, f"Proef afgebroken. {reason}.")
        raise
    write_report(rows, "Sim-getest op twee lokale Custom Apps met een proefmeter en lege Zigbee-bronopslag.")
    print("Herstelproef A naar B afgerond. De brongegevens zijn behouden.")


if __name__ == "__main__":
    main()
