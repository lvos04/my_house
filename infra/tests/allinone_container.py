"""Containerproeven voor de Custom App. Aanroep: python3 ... <lokale-image>."""

import gzip
import json
import os
import secrets
import subprocess
import sys
import textwrap
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID, uuid4

PYTHON = ("/opt/venv/bin/python", "-IBu")
HEALTH = (*PYTHON, "-m", "shc_allinone.healthcheck")
ROOKSCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_test.py"
ROOKPROEF = """
import sys
from shc_allinone.runtime import NGINX_HEALTH

sys.argv[1:1] = ["--base", NGINX_HEALTH.removesuffix("/healthz")]
exec(compile(sys.stdin.buffer.read(), "smoke_test.py", "exec"))
"""
HEALTH_ZONDER_WISSEL = (*PYTHON, "-c", """
from shc_allinone import healthcheck

# Alleen deze exec-proef slaat de rechtenwissel over.
healthcheck.drop_privileges = lambda: None
raise SystemExit(healthcheck.main(["--toon-rechten"]))
""")
API_GESTOPT = """
import signal
import time
from shc_allinone import supervisor

# Laat nginx draaien tijdens de opzettelijke uitval van de proef-API.
def watch(self, directory, config, password, zigbee):
    self.start_services(directory, config, password, zigbee)
    try:
        api = self.children["api"]
        api.terminate()
        assert api.wait(timeout=15) in (0, -signal.SIGTERM)
        print("Proef-API gestopt; nginx draait door.", flush=True)
        while not self.stopping:
            time.sleep(0.25)
    finally:
        self.shutdown()

supervisor.Supervisor.watch = watch
raise SystemExit(supervisor.main())
"""
PG = ("-XAtq", "-v", "ON_ERROR_STOP=1", "-U", "apps", "-h", "/appdata/postgres")
ZOEK_REVISIE = "SELECT version_num FROM alembic_version"
GROEPSPROEF = (
    "chown 0:27 /dev/zigbee && chmod 660 /dev/zigbee && "
    "exec /opt/venv/bin/python -IBu -m shc_allinone.supervisor"
)
DATABASE_ALLEEN = """
import signal
import time
from shc_allinone.runtime import drop_privileges
from shc_allinone.storage import check_layout
from shc_allinone.supervisor import DATA, Supervisor, service_env

drop_privileges()
control = Supervisor(service_env())
control.new_storage = check_layout(DATA)
signal.signal(signal.SIGTERM, control.stop)
try:
    control.database()
    print("Proefdatabase gestart")
    while not control.stopping and not control.failed_child():
        time.sleep(0.1)
finally:
    control.shutdown()
"""
ONDERBROKEN_START = """
import os
import sys
from shc_allinone import storage, supervisor

run = supervisor.Supervisor.run
sync = storage.sync_directory
phase = sys.argv[1]

def stop():
    print("Gesimuleerde onderbreking tijdens " + phase, file=sys.stderr, flush=True)
    os._exit(1)

def interrupt_link(source, target):
    stop()

def interrupt_sync(path):
    sync(path)
    if path == supervisor.DATA / "secrets" and (path / "app-key").is_file():
        stop()

def interrupt(self, name, command):
    if name == phase:
        if name == "initialisatie":
            print("Gesimuleerde onderbreking vóór initialisatie", file=sys.stderr, flush=True)
            os._exit(1)
        raise supervisor.StartFout("Gesimuleerde onderbreking vóór " + name)
    return run(self, name, command)

if phase == "sleutelopslag-voor-link":
    storage.os.link = interrupt_link
elif phase == "sleutelopslag":
    storage.sync_directory = interrupt_sync
else:
    supervisor.Supervisor.run = interrupt
raise SystemExit(supervisor.main())
"""
PROCESBOOM = """
import json
from pathlib import Path

fields = {"PPid", "Uid", "Gid", "Groups", "CapEff", "CapPrm", "CapAmb", "CapBnd", "NoNewPrivs"}
tree = {}
for proc in Path("/proc").iterdir():
    if not proc.name.isdecimal():
        continue
    try:
        status = dict(line.split(":", 1) for line in (proc / "status").read_text().splitlines())
        args = (proc / "cmdline").read_bytes().split(b"\\0")
    except OSError:
        continue
    tree[proc.name] = (
        {k: status[k].split() for k in fields},
        b"index.js" in args, b"shc_allinone.supervisor" in args, b"worker" in args,
    )
selected = {"1"}
while pending := {pid for pid, (status, *_args) in tree.items() if status["PPid"][0] in selected} - selected:
    selected |= pending
print(json.dumps({pid: tree[pid] for pid in selected}))
"""
LUISTERAARS = """
from pathlib import Path

for filename in ("tcp", "tcp6"):
    for line in Path("/proc/net", filename).read_text().splitlines()[1:]:
        fields = line.split()
        if fields[3] == "0A":
            print(fields[1])
"""
PROXYPROEF = """
import json
import secrets
import urllib.request
from websockets.sync.client import connect
from shc_allinone.runtime import NGINX_HEALTH

origin = NGINX_HEALTH.removesuffix("/healthz")
request = urllib.request.Request(
    origin + "/api/v1/auth/setup",
    data=json.dumps({"username": "proefbeheer", "password": secrets.token_urlsafe(32)}).encode(),
    headers={"Content-Type": "application/json", "Origin": origin,
             "X-Forwarded-Proto": "https", "X-Forwarded-For": "onvertrouwd",
             "Forwarded": "proto=https", "CF-Connecting-IP": "onvertrouwd"},
)
with urllib.request.urlopen(request, timeout=5) as response:
    assert response.status == 200
    cookie = response.headers.get("Set-Cookie", "")
    assert "httponly" in cookie.lower()
    assert "secure" not in cookie.lower()
    session = cookie.split(";", 1)[0]
ws = origin.replace("http:", "ws:") + "/api/v1/ws"
with connect(ws, origin=origin, additional_headers={"Cookie": session}) as conn:
    assert json.loads(conn.recv(timeout=5))["type"] == "hello"
with connect(
    ws, origin="http://onvertrouwd.invalid",
    additional_headers={"Cookie": session, "X-Forwarded-Host": "onvertrouwd.invalid"},
) as conn:
    conn.send('{"type":"auth"}')
    assert json.loads(conn.recv(timeout=5))["data"]["reason"] == "origin_mismatch"
"""
VULLING = """
INSERT INTO users (id, username, password_hash, role, disabled, created_at)
  VALUES (gen_random_uuid(), 'bewoner', 'x', 'admin', false, now());
INSERT INTO homes (id, name, timezone, currency, active_from)
  VALUES (gen_random_uuid(), 'Proefwoning', 'UTC', 'EUR', now());
INSERT INTO settings (key, value) VALUES ('s_p3_720', '{"a": 1}');
"""
RIJEN = """
SELECT to_jsonb(u) FROM users u WHERE username = 'bewoner';
SELECT to_jsonb(h) FROM homes h WHERE name = 'Proefwoning';
SELECT value FROM settings WHERE key = 's_p3_720';
"""


def docker(*args, content=None, check=True, timeout=45):
    return subprocess.run(  # noqa: S603 - alleen deze lokale Docker-proef
        ["docker", *args],  # noqa: S607 - Docker uit het lokale testpad
        input=content, capture_output=True, check=check, timeout=timeout,
    )


def within(test, label, seconds=85):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        result = test()
        if result:
            return result
        time.sleep(0.5)
    raise AssertionError(label)


class Trial:
    def __init__(self, image, cleanup, step="725"):
        self.image = image
        self.cleanup = cleanup
        self.prefix = f"my-house-s-p3-{step}-" + uuid4().hex[:10]
        self.broker_profile = os.environ.get("SHC_CI_BROKERPROFIEL") == "1"

    def storage(self, label):
        volume = f"{self.prefix}-{label}"
        docker("volume", "create", volume)
        self.cleanup.callback(docker, "volume", "rm", volume)
        return volume

    def container(self, label, volume=None, user="0", options=(), entrypoint=(), network="none", *,
                  failing_dump=False, terugvalstart=False):
        name = f"{self.prefix}-{label}"
        owner = f"nl.medialeon.herstelproef={name}"
        volume_options = ["--mount", f"type=volume,source={volume},target=/appdata"] if volume else []
        if volume and self.broker_profile:
            # Beide paden verwijzen naar dezelfde brokerdatabase, ook bij een lege eerste start.
            # nocopy voorkomt dat Docker pakketbestanden uit /var/lib aan de proefopslag toevoegt.
            volume_options.extend([
                "--mount", f"type=volume,source={volume},target=/var/lib,volume-nocopy",
                "--tmpfs", "/etc/mosquitto/conf.d:uid=568,gid=568,mode=0700",
            ])
        overrides = ["--entrypoint", entrypoint[0]] if entrypoint else []
        try:
            created = docker(
                "create", "--name", name, "--label", owner, "--network", network, "--user", user,
                *volume_options, *options, *overrides, self.image, *entrypoint[1:], timeout=120,
            )
        except subprocess.TimeoutExpired:
            # De daemon kan de container al hebben aangemaakt voordat de client antwoord krijgt.
            own = docker("ps", "-aq", "--no-trunc", "--filter", f"label={owner}", timeout=120)
            for container_id in own.stdout.decode().split():
                self.cleanup.callback(docker, "rm", "-fv", container_id, timeout=120)
            raise
        container_id = created.stdout.decode().strip()
        self.cleanup.callback(docker, "rm", "-fv", container_id, timeout=120)
        if failing_dump:
            # Alleen deze container krijgt de foutfixture. De proefopslag bevat geen uitvoerbare code.
            fixture = Path(__file__).with_name("pg_dump_fout.sh")
            docker("cp", str(fixture), f"{container_id}:/usr/lib/postgresql/16/bin/pg_dump")
        if terugvalstart:
            # De gedeelde CI-daemon kan geen hostpad uit de werkcontainer koppelen. De kopie in
            # de gestopte proefcontainer is net als de alleen-lezen koppeling buiten bereik van uid 568.
            start = Path(__file__).resolve().parents[2] / "apps/api/src/shc_api/terugval_start.py"
            docker("cp", str(start), f"{container_id}:/opt/venv/bin/shc-api")
        docker("start", container_id, timeout=120)
        return name

    def volume_python(self, label, volume, script, *args):
        name = self.container(label, volume, user="568:568", options=("--no-healthcheck",),
                              entrypoint=(*PYTHON, "-c", script, *args))
        assert docker("wait", name).stdout.strip() == b"0", f"Opslagproef {label} afgebroken"
        return docker("logs", name).stdout

    @staticmethod
    def exec(name, *cmd, content=None, check=True, user="568", timeout=45):
        return docker("exec", "-i", "--user", user, name, *cmd, content=content, check=check, timeout=timeout)

    @staticmethod
    def logs(name):
        result = docker("logs", name)
        return result.stdout + result.stderr

    def state(self, name):
        return docker(
            "inspect", "--format",
            "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", name,
        ).stdout.decode().strip()

    def healthy(self, name):
        def status():
            state = self.state(name)
            if not state.startswith("running "):
                # Alleen vaste foutcategorieën mogen het CI-log in.
                logs = self.logs(name)
                markers = (
                    b"Unable to open config file", b"Unable to open pwfile", b"Unable to open acl_file",
                    b"Address already in use",
                    b"PostgreSQL is niet beschikbaar", b"API is niet beschikbaar", b"MQTT-broker is niet beschikbaar",
                    b"Database-update geweigerd", b"De eerste database-initialisatie kan niet veilig doorgaan",
                    b"Opstarten geweigerd", b"Opstarten mislukt",
                )
                reason = next((marker.decode() for marker in markers if marker in logs), "geen bekende opstartmelding")
                raise AssertionError(f"{name}: containerstatus {state} ({reason})")
            return state == "running healthy"
        within(status, f"{name}: healthcheck werd niet groen")

    def sql(self, name, query, database="shc"):
        return self.exec(name, "psql", *PG, "-d", database, content=query.encode()).stdout

    def db_fixture(self, label, volume):
        name = self.container(label, volume, user="568", options=("--no-healthcheck",),
                              entrypoint=(*PYTHON, "-c", DATABASE_ALLEEN))
        within(lambda: b"Proefdatabase gestart" in self.logs(name), "De socket-proefdatabase kwam niet op")
        return name

    def processes(self, name):
        return json.loads(self.exec(name, *PYTHON, "-c", PROCESBOOM).stdout)

    @staticmethod
    def identity(status, root, groups):
        assert set(status["Uid"] + status["Gid"]) == {"568"}
        assert set(status["Groups"]) <= groups
        assert status["NoNewPrivs"] == ["1"]
        assert all(int(status[s][0], 16) == 0 for s in ("CapEff", "CapPrm", "CapAmb"))
        if root:
            assert int(status["CapBnd"][0], 16) == 0

    def service_rights(self, name, *, root, zigbee=False):
        def sample():
            tree = self.processes(name)
            if zigbee and not any(is_node for _, is_node, *_ in tree.values()):
                return None
            return tree

        tree = within(sample, "Het Zigbee2MQTT-proces was niet te meten", seconds=40)
        assert not tree["1"][1] and tree["1"][2]
        assert len(tree) >= 7
        assert any(worker for _, _, _, worker in tree.values())
        for pid, (status, is_node, _, _) in tree.items():
            allowed = {"568"}
            ancestor = pid
            while ancestor != "1":
                pstat, node, guardian, _ = tree[ancestor]
                if node or guardian:
                    allowed.add("27")
                ancestor = pstat["PPid"][0]
            self.identity(status, root, allowed if zigbee else {"568"})
            if is_node:
                assert zigbee and status["Groups"] == ["27"]
            if pid == "1":
                assert not status["Groups"] or status["Groups"] == ["568"]

        self.healthcheck_rights(name, root=root)

    def healthcheck_rights(self, name, *, root, probe=(*HEALTH, "--toon-rechten")):
        result = self.exec(name, *probe, user="0" if root else "568")
        health_rights = dict((key, value.split()) for key, value in
                             (line.split(":", 1) for line in result.stdout.decode().splitlines()))
        self.identity(health_rights, root, {"568"})

    def healthcheck_without_drop(self, name):
        try:
            self.healthcheck_rights(name, root=True, probe=HEALTH_ZONDER_WISSEL)
        except AssertionError:
            print("Rechtenproef zonder wissel: geweigerd.", flush=True)
        else:
            raise AssertionError("De rechtenproef liet een healthcheck zonder wissel door.")
        self.healthcheck_rights(name, root=True)
        print("Rechtenproef met herstelde wissel: gebruiker 568.", flush=True)

    def smoke(self, name, *args):
        result = self.exec(
            name, *PYTHON, "-c", ROOKPROEF, *args, content=ROOKSCRIPT.read_bytes(), check=False, timeout=360,
        )
        print(result.stdout.decode().rstrip(), flush=True)
        assert result.returncode == 0, result.stderr.decode().rstrip()

    def smoke_on_clean_storage(self):
        # De rooktest maakt een beheerder en woning; de migratieproef gebruikt haar eigen gegevens.
        name = self.container("rooktest", self.storage("rooktest"), user="568")
        self.healthy(name)
        self.smoke(name, "--zonder-zigbee")
        docker("stop", name)

    def unavailable(self):
        name = self.container("api-uit", self.storage("api-uit"), entrypoint=(*PYTHON, "-c", API_GESTOPT))
        within(lambda: b"Proef-API gestopt; nginx draait door." in self.logs(name), "De proef-API stopte niet.")
        self.smoke(name, "--expect-unavailable")
        code = secrets.token_urlsafe(24)
        request = """
import http.client, sys
conn = http.client.HTTPConnection("localhost", 8080, timeout=5)
conn.request("GET", "/api/v1/system/restore/jobs/" + sys.stdin.read())
assert conn.getresponse().status == 503
conn.close()
"""
        self.exec(name, *PYTHON, "-c", request, content=code.encode())
        if code.encode() in self.logs(name):
            raise AssertionError("De proxyfout bij een onbereikbaar taakrapport bevat de proefcode")
        assert self.exec(name, *HEALTH, check=False).returncode == 1
        docker("stop", name)

    def listeners(self, name):
        rows = self.exec(name, *PYTHON, "-c", LUISTERAARS).stdout.decode().splitlines()
        assert sorted(rows) == sorted(("00000000:1F90", "0100007F:1F40", "0100007F:075B"))
        assert docker("inspect", "--format", "{{json .Config.ExposedPorts}}", name).stdout.strip() == b'{"8080/tcp":{}}'

    def backup(self, name, *, zigbee):
        code = """
import os
from shc_allinone.supervisor import service_env
os.execvpe("shc-api", ["shc-api", "backup", "--passphrase-stdin", "--json"], service_env())
"""
        result = self.exec(name, *PYTHON, "-c", code, content=(secrets.token_urlsafe(32) + "\n").encode())
        assert json.loads(result.stdout)["zigbee2mqtt_included"] is zigbee

    def zigbee_file_boundary(self):
        # Dit kleine Node-programma doorloopt dezelfde fork, startvolgorde en rechtenwissel als Zigbee2MQTT.
        roots = ["/appdata", "/var/lib"] if self.broker_profile else ["/appdata"]
        script = "const roots = " + json.dumps(roots) + ";\n" + """
const fs = require("node:fs");
function denied(name) {
  try { fs.closeSync(fs.openSync(name, "r")); }
  catch (e) { return e.code === "EACCES" || e.code === "EPERM"; }
  return false;
}
const own = process.env.ZIGBEE2MQTT_DATA + "/secret.yaml";
const files = ["secrets/app-key", "secrets/mqtt-shc", "postgres/data/PG_VERSION"];
const restricted = roots.every(root => files.every(file => denied(root + "/" + file)));
fs.closeSync(fs.openSync(own, "r"));
console.log(restricted && !process.env.PGPROEF && !process.env.SHC_PROEF
  ? "Zigbee-bestandsgrens bevestigd." : "Zigbee-bestandsgrens ontbreekt.");
"""
        setup = textwrap.dedent("""
            from pathlib import Path
            from shc_allinone import supervisor
            import os, sys
            supervisor.ZIGBEE_JS = Path("/opt/zigbee-leesproef")
            supervisor.ZIGBEE_JS.mkdir()
            (supervisor.ZIGBEE_JS / "index.js").write_text(sys.argv[1])
            os.environ.update(PGPROEF="aanwezig", SHC_PROEF="aanwezig")
            raise SystemExit(supervisor.main())
        """)
        name = self.container("bestandsgrens", self.storage("leesproef"), options=("--device", "/dev/null:/dev/zigbee"),
                              entrypoint=(*PYTHON, "-c", setup, script))
        within(lambda: b"Zigbee-bestandsgrens " in self.logs(name), "De Node-leesproef gaf geen uitslag")
        assert b"Zigbee-bestandsgrens bevestigd." in self.logs(name), "De Zigbee-bestandsgrens ontbreekt"
        self.healthy(name)
        docker("stop", name)
        print("Node leest eigen Zigbee-opslag. Appgeheimen en databasebestanden zijn afgeschermd.", flush=True)

    def direct_device(self):
        name = self.container("direct-apparaat", self.storage("direct-apparaat"), user="568:568",
                              options=("--device", "/dev/null:/dev/zigbee"))
        self.healthy(name)
        tree = self.processes(name)
        for status, *_ in tree.values():
            self.identity(status, root=False, groups={"568"})
        if int(tree["1"][0]["CapBnd"][0], 16):
            assert not any(node for _, node, *_ in tree.values())
            assert b"bounding set" in self.logs(name)
        else:
            within(lambda: any(node for _, node, *_ in self.processes(name).values()), "Veilige Zigbee-start ontbreekt")
        docker("stop", name)
        print("Directe 568-start met apparaat: bounding set getoetst, kernapp gezond.", flush=True)

    def zigbee_status(self, name):
        # De volgende start leest een rij zoals opgeslagen door de vorige installatie.
        self.sql(name, """
            WITH woning AS (
              INSERT INTO homes (id,name,timezone,currency,active_from)
              VALUES (gen_random_uuid(),'Zigbeeproef','UTC','EUR',now()) RETURNING id
            )
            INSERT INTO integrations(id,home_id,provider,name,config,state,detail,enabled,created_at)
            SELECT gen_random_uuid(),id,'zigbee','Zigbeeproef','{"host":"mosquitto","port":1883,"username":"shc"}',
                   'warn','',true,now() FROM woning;
        """)
        docker("restart", name, "--time", "10")
        self.healthy(name)
        ready = """SELECT count(*) FROM integrations WHERE name='Zigbeeproef'
                   AND diagnostics #>> '{health,extra,mqtt_connected}' = 'true'"""
        within(lambda: self.sql(name, ready).strip() == b"1", "De worker bereikte de interne broker niet", seconds=150)
        check = """
import httpx, secrets
from shc_allinone.runtime import NGINX_HEALTH
with httpx.Client(base_url=NGINX_HEALTH.removesuffix("/healthz"), trust_env=False) as client:
    login = client.post("/api/v1/auth/setup", json={"username":"proefbeheer", "password":secrets.token_urlsafe(32)})
    login.raise_for_status()
    reply = client.get("/api/v1/system/status")
    reply.raise_for_status()
    parts = {row["key"].split(":")[-1]:row["state"] for row in reply.json()["checks"]}
    assert parts["mqtt"] == "ok" and parts["dongle"] in {"error", "unknown"}
"""
        self.exec(name, *PYTHON, "-c", check)
        self.exec(name, *HEALTH)
        print("Opgeslagen brokernaam werkt; de statusroute meldt de falende proefdongle.", flush=True)

    def file(self, name, target):
        result = self.exec(name, "cat", f"/appdata/{target}")
        return result.stdout

    def missing_key(self, name, volume):
        check = """
import asyncio, os, sys
from shc_allinone.supervisor import service_env
os.environ.update(service_env())
from shc_api.db import sessionmaker
from shc_api.store import get_secret_setting, set_secret_setting
async def check():
    value = sys.stdin.read()
    async with sessionmaker()() as db:
        if sys.argv[1] == "schrijf":
            await set_secret_setting(db, "sleutelproef", value)
            await db.commit()
        assert await get_secret_setting(db, "sleutelproef") == value
asyncio.run(check())
"""
        value = secrets.token_urlsafe(32).encode()
        self.exec(name, *PYTHON, "-c", check, "schrijf", content=value)
        self.exec(name, "mv", "/appdata/secrets/app-key", "/appdata/secrets/app-key.proef")
        docker("restart", name, "--time", "10")
        assert docker("wait", name).stdout.strip() == b"1"
        assert b"app-key" in self.logs(name)
        restore = """
from pathlib import Path
key = Path("/appdata/secrets/app-key")
assert not key.exists()
key.hardlink_to(key.with_suffix(".proef"))
key.with_suffix(".proef").unlink()
"""
        self.volume_python("herstel-sleutel", volume, restore)
        docker("start", name)
        self.healthy(name)
        self.exec(name, *PYTHON, "-c", check, "lees", content=value)
        print("Start zonder sleutel geweigerd. De oorspronkelijke sleutel ontsluit dezelfde proefwaarde.", flush=True)

    def installed(self, root, zigbee=False):
        label = "zigbee" if zigbee else "root" if root else "direct"
        volume = self.storage(label)
        opts = ("--device", "/dev/null:/dev/zigbee",) if zigbee else ()
        entrypoint = ("/bin/sh", "-ec", GROEPSPROEF) if zigbee else ()
        name = self.container(label, volume, "0" if root else "568", opts, entrypoint)
        self.healthy(name)
        self.service_rights(name, root=root, zigbee=zigbee)
        self.listeners(name)
        paths = ["secrets/app-key", "secrets/mqtt-shc"]
        if zigbee:
            within(lambda: b"Nieuwe poging over 4 seconden." in self.logs(name), "Zigbee-herstart vertraagde niet")
            self.healthy(name)
            self.exec(name, *HEALTH)
            self.zigbee_status(name)
            self.exec(
                name, "/bin/sh", "-c",
                'printf "\\n# Bestaande configuratie\\n" >> /appdata/zigbee2mqtt/configuration.yaml',
            )
            paths += ["zigbee2mqtt/configuration.yaml", "zigbee2mqtt/secret.yaml"]
        else:
            assert b"Zigbee2MQTT" not in self.logs(name)
            assert self.exec(name, "test", "!", "-e", "/appdata/zigbee2mqtt").returncode == 0
        values = {path: self.file(name, path) for path in paths}
        for value in values.values():
            assert value.strip() and value.strip() not in self.logs(name)
        assert self.exec(
            name, "test", "-r", "/appdata/secrets/app-key", user="65534:65534", check=False,
        ).returncode == 1
        self.backup(name, zigbee=zigbee)
        if root and not zigbee:
            self.exec(name, *PYTHON, "-c", PROXYPROEF)
            self.healthcheck_without_drop(name)
        docker("restart", name, "--time", "10")
        self.healthy(name)
        assert all(self.file(name, path) == value for path, value in values.items())
        if zigbee:
            password_line = values["zigbee2mqtt/secret.yaml"].decode().split("'")[1]
            assert password_line.encode() not in self.logs(name)
            code = """from pathlib import Path
from shc_allinone.supervisor import broker_accepts
password = Path("/appdata/zigbee2mqtt/secret.yaml").read_text().split("'")[1]
assert broker_accepts(password, "zigbee2mqtt")
"""
            self.exec(name, *PYTHON, "-c", code)
        if not root:
            self.missing_key(name, volume)
        print(f"Eerste start en behoud bij herstart: {label}", flush=True)
        docker("stop", name)
        return volume

    def interrupted_first_start(self):
        volume = self.storage("onderbroken")
        before = None
        for phase in ("sleutelopslag-voor-link", "sleutelopslag", "initialisatie", "databaseaanmaak"):
            interrupted = self.container(f"voor-{phase}", volume, options=("--no-healthcheck",),
                                         entrypoint=(*PYTHON, "-c", ONDERBROKEN_START, phase))
            assert docker("wait", interrupted).stdout.strip() == b"1"
            moment = "tijdens" if phase.startswith("sleutelopslag") else "vóór"
            assert f"Gesimuleerde onderbreking {moment} {phase}".encode() in self.logs(interrupted)
            keys = """
from pathlib import Path
from hashlib import sha256
from stat import S_IMODE
import sys

data = Path("/appdata")
phase = sys.argv[1]
assert (data / "postgres/data/PG_VERSION").is_file() == (phase in ("databaseaanmaak", "hervat"))
secret_dir = data / "secrets"
if phase.startswith("sleutelopslag"):
    assert not any(p.is_file() and p.parent != secret_dir for p in data.rglob("*"))
    assert S_IMODE(secret_dir.stat().st_mode) == 0o700
    assert not (secret_dir / "mqtt-shc").exists()
    (pending,) = secret_dir.glob(".nieuw-app-key-*")
    info = pending.stat()
    assert (info.st_uid, info.st_gid, S_IMODE(info.st_mode)) == (568, 568, 0o600)
    key = secret_dir / "app-key"
    if phase == "sleutelopslag-voor-link":
        assert not key.exists() and info.st_nlink == 1
        names = ()
    else:
        assert pending.samefile(key) and info.st_nlink == 2
        names = ("app-key",)
else:
    names = ("app-key", "mqtt-shc")
    assert {p.name for p in secret_dir.iterdir()} == set(names)
for name in names:
    path = secret_dir / name
    print(path.stat().st_ino, sha256(path.read_bytes()).hexdigest())
"""
            contents = self.volume_python(f"controle-{phase}", volume, keys, phase)
            if before is not None:
                assert contents.startswith(before)
            before = contents
        resumed = self.container("hervat", volume)
        self.healthy(resumed)
        assert self.exec(resumed, *PYTHON, "-c", keys, "hervat").stdout == before
        assert self.sql(resumed, ZOEK_REVISIE).strip()
        assert self.exec(resumed, "test", "!", "-e", "/appdata/.eerste-start").returncode == 0
        docker("stop", resumed)
        print("Eerste start hervat vóór publicatie en na mapsynchronisatie in secrets, vóór initdb en "
              "vóór databaseaanmaak. Vastgelegde sleutels behielden inhoud en inode.", flush=True)

    def incomplete_zigbee_storage(self):
        volume = self.storage("zigbee-onvolledig")
        core = self.container("zigbee-opslag-kern", volume)
        self.healthy(core)
        config = b"# Bestaande proefconfiguratie\n"
        self.exec(core, "/bin/sh", "-ec", "umask 077; mkdir /appdata/zigbee2mqtt; "
                  "cat > /appdata/zigbee2mqtt/configuration.yaml", content=config)
        docker("stop", core)
        partial = self.container("zigbee-onvolledig", volume, options=("--device", "/dev/null:/dev/zigbee"),
                                 entrypoint=("/bin/sh", "-ec", GROEPSPROEF))
        self.healthy(partial)
        assert b"Zigbee2MQTT blijft uit: De Zigbee-opslag is onvolledig" in self.logs(partial)
        assert not any(node for _, node, _, _ in self.processes(partial).values())
        assert self.file(partial, "zigbee2mqtt/configuration.yaml") == config
        assert self.exec(partial, "test", "!", "-e", "/appdata/zigbee2mqtt/secret.yaml").returncode == 0
        docker("stop", partial)
        print("Onvolledige Zigbee-opslag: dienst geweigerd, kern gezond en configuratie bytegelijk.", flush=True)

    def snapshot(self, name, database):
        tables = self.sql(name, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1;", database)
        statements = []
        for table in tables.decode().splitlines():
            assert table.replace("_", "").isalnum()
            statements.append(
                f"""SELECT '{table}', count(*), md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), ''))
                    FROM "{table}" x;"""  # noqa: S608 - tabelnaam uit de proefdatabase
            )
        assert len(statements) > 10
        return self.sql(name, "\n".join(statements), database)

    def prepare_fallback(self, label, volume, expected_code, message):
        name = self.container(label, volume, user="568", options=("--no-healthcheck",),
                              entrypoint=(*PYTHON, "-m", "shc_allinone.terugval"))
        assert docker("wait", name, timeout=90).stdout.strip() == str(expected_code).encode()
        assert message.encode() in self.logs(name)
        if expected_code == 0:
            regel, = [s for s in self.logs(name).decode().splitlines() if s.startswith("Terugval-ID: ")]
            return str(UUID(regel.removeprefix("Terugval-ID: ")))

    def guarded_fallback(self, volume):
        entry = (*PYTHON, "/opt/venv/bin/shc-api", "terugval-app")
        missing = self.container("zonder-startbestand", volume, user="568", entrypoint=entry)
        assert docker("wait", missing).stdout.strip() == b"2"
        refused = self.container("zonder-vrijgave", volume, user="568", entrypoint=entry, terugvalstart=True,
                                 options=("--env", f"SHC_TERUGVAL_ID={uuid4()}"))
        assert docker("wait", refused, timeout=90).stdout.strip() == b"1"
        assert b"Terugval geweigerd" in self.logs(refused)
        receipt = self.prepare_fallback("vrijgave-0009", volume, 0, "Terugvalkopie voorbereid. PostgreSQL is gestopt.")
        accepted = self.container("met-vrijgave", volume, user="568", entrypoint=entry, terugvalstart=True,
                                  options=("--env", f"SHC_TERUGVAL_ID={receipt}"))
        self.healthy(accepted)
        assert self.sql(accepted, RIJEN).count(b"\n") == 3
        docker("stop", accepted)

    def migration(self, volume):
        db = self.db_fixture("voor", volume)
        module = self.exec(db, *PYTHON, "-m", "shc_allinone.startpad").stdout.decode().strip()
        assert module.startswith("shc_allinone.start_") and len(bytes.fromhex(module.split("_")[-1])) == 32
        downgrade = """
from alembic import command
from shc_api.backup import alembic_config
from shc_allinone.supervisor import service_env
command.downgrade(alembic_config(service_env()["SHC_DATABASE_URL"]), "0003")
"""
        self.exec(db, *PYTHON, "-c", downgrade)
        self.sql(db, VULLING)
        before_rows = self.sql(db, RIJEN)
        assert len(before_rows.splitlines()) == 3
        before_dump = self.snapshot(db, "shc")
        assert self.sql(db, ZOEK_REVISIE).strip() == b"0003"
        assert not self.sql(db, "SHOW listen_addresses").strip()
        assert self.sql(db, "SHOW server_version_num").strip().startswith(b"16")
        docker("stop", db)
        self.prepare_fallback("terugval-0003", volume, 0, "Terugvalkopie voorbereid. PostgreSQL is gestopt.")

        bad = self.container("dumpfout", volume, user="568", failing_dump=True)
        within(lambda: self.state(bad).startswith("exited"), "Mislukte dump hield de start niet tegen")
        assert b"Melding van pg_dump: 27" in self.logs(bad)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", bad).stdout.strip() == b"1"
        db = self.db_fixture("na-weigering", volume)
        assert self.snapshot(db, "shc") == before_dump
        docker("stop", db)

        upgraded = self.container("upgrade", volume, entrypoint=(*PYTHON, "-m", module))
        self.healthy(upgraded)
        head = self.sql(upgraded, ZOEK_REVISIE).strip()
        assert head != b"0003"
        assert self.sql(upgraded, RIJEN) == before_rows
        dumps = self.exec(upgraded, "/bin/sh", "-ec", "ls /appdata/backups/pre-migrate-*.sql.gz").stdout.splitlines()
        assert len(dumps) == 1
        backup = self.exec(upgraded, "cat", dumps[0].decode()).stdout
        assert b"bewoner" in gzip.decompress(backup)
        self.exec(upgraded, "shc-api", "verify-migration-dump", dumps[0].decode(), "--out", "/appdata/app/herstel.sql")
        self.exec(upgraded, "createdb", "-h", "/appdata/postgres", "my_house_s_p3_720_herstel")
        self.exec(upgraded, "psql", *PG, "-d", "my_house_s_p3_720_herstel", "-f", "/appdata/app/herstel.sql")
        assert self.snapshot(upgraded, "my_house_s_p3_720_herstel") == before_dump
        print("PostgreSQL 16 via unix-socket: dumpweigering, gevulde upgrade en exact terugzetten bewezen.", flush=True)

        docker("stop", upgraded)
        self.guarded_fallback(volume)
        db = self.db_fixture("toekomst", volume)
        self.sql(db, "UPDATE alembic_version SET version_num = 'proef_toekomst'")
        before_recovery = self.snapshot(db, "shc")
        docker("stop", db)
        self.prepare_fallback("terugval-onbekend", volume, 1, "De oude worker blijft uit.")
        recovery = self.container("herstel", volume)
        code = """
from shc_allinone.runtime import NGINX_HEALTH, http_status
base = NGINX_HEALTH.removesuffix("/healthz")
assert http_status(base + "/api/v1/system/recovery") == 200
assert http_status(base + "/healthz") == 200
assert http_status(base + "/readyz") == 503
"""
        within(lambda: self.exec(recovery, *PYTHON, "-c", code, check=False).returncode == 0,
               "Herstelinfo bleef onbereikbaar")
        assert self.exec(recovery, *HEALTH, check=False).returncode == 1
        assert self.exec(recovery, "test", "!", "-e", "/appdata/app/worker-alive").returncode == 0
        assert not any(worker for _, _, _, worker in self.processes(recovery).values())
        assert self.snapshot(recovery, "shc") == before_recovery
        after = self.exec(recovery, "/bin/sh", "-ec", "ls /appdata/backups/pre-migrate-*.sql.gz").stdout.splitlines()
        assert after == dumps
        print("Herstelmodus: proxy bereikbaar, readiness rood en database ongemoeid.", flush=True)

    def refusals(self):
        no_capability = self.container("geen-wissel", options=("--cap-drop", "SETUID"), entrypoint=HEALTH)
        assert docker("wait", no_capability).stdout.strip() == b"1"
        assert b"rechtenwissel" in self.logs(no_capability)
        bad_storage = self.container("geen-opslag", user="568", options=("--read-only",))
        assert docker("wait", bad_storage).stdout.strip() == b"1"
        assert b"schrijfbaar" in self.logs(bad_storage)


def main():
    with ExitStack() as cleanup:
        trial = Trial(sys.argv[1], cleanup)
        trial.refusals()
        trial.interrupted_first_start()
        trial.incomplete_zigbee_storage()
        trial.installed(root=True)
        upgrade_volume = trial.installed(root=False)
        trial.installed(root=True, zigbee=True)
        trial.smoke_on_clean_storage()
        trial.unavailable()
        trial.direct_device()
        trial.zigbee_file_boundary()
        trial.migration(upgrade_volume)
    print("Containerproeven voor de Custom App afgerond.")


if __name__ == "__main__":
    main()
