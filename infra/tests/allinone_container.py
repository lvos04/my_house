"""Gerichte containerproeven voor S-P3-720. Aanroep: python3 ... <lokale-image>."""

import gzip
import json
import secrets
import subprocess
import sys
import time
from contextlib import ExitStack
from uuid import uuid4

PYTHON = ("/opt/venv/bin/python", "-IBu")
HEALTH = (*PYTHON, "-m", "shc_allinone.healthcheck")
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
from shc_allinone.supervisor import Supervisor, service_env

drop_privileges()
control = Supervisor(service_env())
signal.signal(signal.SIGTERM, control.stop)
try:
    control.database()
    print("Proefdatabase gestart")
    while not control.stopping and not control.failed_child():
        time.sleep(0.1)
finally:
    control.shutdown()
"""
MISLUKTE_DUMP = """
from shc_allinone import supervisor
from shc_allinone.runtime import DATA

prepare = supervisor.prepare_secrets
env = supervisor.service_env
bin_dir = DATA / "app"
tool = bin_dir / "pg_dump"

def prepare_proef(data):
    password = prepare(data)
    tool.write_text("#!/bin/sh\\nexit 27\\n")
    tool.chmod(0o700)
    return password

supervisor.prepare_secrets = prepare_proef
supervisor.service_env = lambda: {**env(), "PATH": str(bin_dir) + ":" + env()["PATH"]}
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


def docker(*args, content=None, check=True):
    return subprocess.run(  # noqa: S603 - alleen deze lokale Docker-proef
        ["docker", *args],  # noqa: S607 - Docker uit het lokale testpad
        input=content, capture_output=True, check=check, timeout=45,
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
    def __init__(self, image, cleanup):
        self.image = image
        self.cleanup = cleanup
        self.prefix = "my-house-s-p3-720-" + uuid4().hex[:10]

    def storage(self, label):
        volume = f"{self.prefix}-{label}"
        docker("volume", "create", volume)
        self.cleanup.callback(docker, "volume", "rm", volume)
        return volume

    def container(self, label, volume=None, user="0", options=(), entrypoint=()):
        name = f"{self.prefix}-{label}"
        volume_options = ["--mount", f"type=volume,source={volume},target=/appdata"] if volume else []
        overrides = ["--entrypoint", entrypoint[0]] if entrypoint else []
        docker(
            "run", "-d", "--name", name, "--network", "none", "--user", user, *volume_options,
            *options, *overrides, self.image, *entrypoint[1:],
        )
        self.cleanup.callback(docker, "rm", "-fv", name)
        return name

    @staticmethod
    def exec(name, *cmd, content=None, check=True, user="568"):
        return docker("exec", "-i", "--user", user, name, *cmd, content=content, check=check)

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
                # Geen onbewerkte dienstlogs uitsturen. De uitslag bevat de status van de proefcontainer.
                raise AssertionError(f"{name}: containerstatus {state}")
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

        result = self.exec(name, *HEALTH, "--toon-rechten", user="0" if root else "568")
        health_rights = dict((key, value.split()) for key, value in
                             (line.split(":", 1) for line in result.stdout.decode().splitlines()))
        self.identity(health_rights, root, {"568"})

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

    def file(self, name, target):
        result = self.exec(name, "cat", f"/appdata/{target}")
        return result.stdout

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
        print(f"Eerste start en behoud bij herstart: {label}", flush=True)
        docker("stop", name)
        return volume

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

    def migration(self, volume):
        db = self.db_fixture("voor", volume)
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

        bad = self.container("dumpfout", volume, user="568", entrypoint=(*PYTHON, "-c", MISLUKTE_DUMP))
        within(lambda: self.state(bad).startswith("exited"), "Mislukte dump hield de start niet tegen")
        assert b"Melding van pg_dump: 27" in self.logs(bad)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", bad).stdout.strip() == b"1"
        db = self.db_fixture("na-weigering", volume)
        assert self.snapshot(db, "shc") == before_dump
        docker("stop", db)

        upgraded = self.container("upgrade", volume)
        self.healthy(upgraded)
        head = self.sql(upgraded, ZOEK_REVISIE).strip()
        assert head != b"0003"
        assert self.sql(upgraded, RIJEN) == before_rows
        dumps = self.exec(upgraded, "/bin/sh", "-ec", "ls /appdata/backups/pre-migrate-*.sql.gz").stdout.splitlines()
        assert len(dumps) == 1
        backup = self.exec(upgraded, "cat", dumps[0].decode()).stdout
        assert b"bewoner" in gzip.decompress(backup)
        self.exec(upgraded, "createdb", "-h", "/appdata/postgres", "my_house_s_p3_720_herstel")
        self.exec(upgraded, "psql", *PG, "-d", "my_house_s_p3_720_herstel", content=gzip.decompress(backup))
        assert self.snapshot(upgraded, "my_house_s_p3_720_herstel") == before_dump
        print("PostgreSQL 16 via unix-socket: dumpweigering, gevulde upgrade en exact terugzetten bewezen.", flush=True)

        docker("stop", upgraded)
        db = self.db_fixture("toekomst", volume)
        self.sql(db, "UPDATE alembic_version SET version_num = 'proef_toekomst'")
        before_recovery = self.snapshot(db, "shc")
        docker("stop", db)
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
        trial.installed(root=True)
        upgrade_volume = trial.installed(root=False)
        trial.installed(root=True, zigbee=True)
        trial.migration(upgrade_volume)
    print("Containerproeven S-P3-720 afgerond.")


if __name__ == "__main__":
    main()
