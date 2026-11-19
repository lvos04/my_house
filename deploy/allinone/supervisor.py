"""PID 1, opslag, startvolgorde en herstart van de Custom App."""

import contextlib
import fcntl
import os
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import runtime
from .runtime import ALIVE, API_READY, DATA, GID, PG_SOCKET, RECOVERY, UID, drop_privileges, http_status
from .storage import (
    ZIGBEE,
    check_layout,
    finish_first_start,
    prepare_broker,
    prepare_secrets,
    prepare_web,
    prepare_zigbee,
    sync_directory,
    transfer_ownership,
)
from .zigbee_sandbox import restrict_files

SCHEMA_NIEUWER = 3
ZIGBEE_JS = Path("/opt/zigbee2mqtt")
ZIGBEE_RUNTIME = Path("/opt/zigbee-runtime")


class StartFout(Exception):
    pass


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def zigbee_bounding_set_empty() -> bool:
    try:
        return int(runtime.identity()["CapBnd"], 16) == 0
    except (OSError, KeyError, ValueError):
        return False


def zigbee_groups(path: Path, *, root: bool) -> tuple[int, ...] | None:
    try:
        device = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISCHR(device.st_mode):
        return None
    if not root:
        if not zigbee_bounding_set_empty():
            log("Zigbee2MQTT blijft uit: een lege bounding set voor gebruiker 568 is vereist.")
            return None
        if os.access(path, os.R_OK | os.W_OK):
            return ()
    else:
        mode = device.st_mode
        if device.st_uid == UID:
            if mode & 0o600 == 0o600:
                return ()
        elif device.st_gid == GID:
            if mode & 0o060 == 0o060:
                return ()
        elif mode & 0o006 == 0o006:
            return ()
        elif device.st_gid != 0 and mode & 0o060 == 0o060:
            return (device.st_gid,)
    log("Zigbee2MQTT blijft uit: gebruiker 568 kan het apparaat niet lezen en schrijven.")
    return None


def broker_accepts(password: str, username: str = "shc") -> bool:
    from paho.mqtt.client import CallbackAPIVersion, Client

    accepted = None

    def connected(_client, _userdata, _flags, reason, _properties):
        nonlocal accepted
        accepted = not reason.is_failure

    client = Client(CallbackAPIVersion.VERSION2)
    client.on_connect = connected
    client.username_pw_set(username, password)
    try:
        client.connect("127.0.0.1", 1883, keepalive=10)
        deadline = time.monotonic() + 1
        while accepted is None and time.monotonic() < deadline and client.loop(timeout=0.2) == 0:
            pass
        return accepted is True
    except OSError:
        return False
    finally:
        client.disconnect()


def signal_group(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, sig)


def stop_child(process: subprocess.Popen, *, postgres: bool = False) -> None:
    if postgres:
        process.send_signal(signal.SIGINT)  # PostgreSQL: stop verbindingen en maak een checkpoint.
    else:
        signal_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        signal_group(process.pid, signal.SIGKILL)
        process.wait()
    if not postgres:
        signal_group(process.pid, signal.SIGKILL)  # Ruim eventueel achtergebleven nakomelingen op.


class Zigbee:
    """Eén bewaker erft uitsluitend de eventueel benodigde apparaatgroep."""

    def __init__(self, groups: tuple[int, ...], storage_fd: int | None = None):
        read_fd, self.write_fd = os.pipe()
        self.pid = os.fork()
        if self.pid:
            os.close(read_fd)
            return
        os.close(self.write_fd)
        try:
            if storage_fd is not None:
                os.close(storage_fd)
            os.setsid()
            drop_privileges(groups)
            os.environ.clear()
            if not zigbee_bounding_set_empty():
                log("Zigbee2MQTT blijft uit: een lege bounding set voor gebruiker 568 is vereist.")
                return
            if not os.access(ZIGBEE, os.R_OK | os.W_OK):
                log("Zigbee2MQTT blijft uit: gebruiker 568 heeft geen toegang tot het apparaat.")
                return
            # De ouder maakt de Zigbee-map als 568. Het eerste plusteken volgt na de brokeraanmelding.
            while command := os.read(read_fd, 1):
                if command == b"+":
                    try:
                        restrict_files(ZIGBEE_JS, ZIGBEE_RUNTIME, DATA / "zigbee2mqtt", ZIGBEE)
                    except OSError:
                        log("Zigbee2MQTT blijft uit: de kernel kan de bestandstoegang niet veilig begrenzen.")
                        return
                    self.watch(read_fd, enabled=True)
                    break
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            log("Zigbee2MQTT blijft uit: de veilige start is mislukt.")
        finally:
            os._exit(0)

    def enable(self, enabled: bool) -> None:
        with contextlib.suppress(BrokenPipeError):
            os.write(self.write_fd, b"+" if enabled else b"-")

    def close(self) -> None:
        os.close(self.write_fd)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(self.pid, 0)

    @staticmethod
    def watch(read_fd: int, *, enabled: bool = False) -> None:
        # Geen geheim in argumenten: de broker leest een hashbestand, Node leest secret.yaml.
        env = dict(
            LD_LIBRARY_PATH=f"{ZIGBEE_RUNTIME}/lib:{ZIGBEE_RUNTIME}/usr/lib",
            ZIGBEE2MQTT_DATA=str(DATA / "zigbee2mqtt"),
            ZIGBEE2MQTT_CONFIG_FRONTEND_ENABLED="false",
            ZIGBEE2MQTT_CONFIG_MQTT_SERVER="mqtt://127.0.0.1:1883",
            ZIGBEE2MQTT_CONFIG_SERIAL_PORT=str(ZIGBEE),
            Z2M_ONBOARD_NO_SERVER="1",
            NODE_ENV="production",
        )
        process = None
        start_at, delay = 0.0, 2
        try:
            while True:
                if select.select([read_fd], [], [], 0.25)[0]:
                    commands = os.read(read_fd, 64)
                    if not commands:
                        return
                    if b"-" in commands and process is not None:
                        stop_child(process)
                        process = None
                    enabled = commands[-1:] == b"+"
                now = time.monotonic()
                if process is not None and process.poll() is not None:
                    stop_child(process)
                    process = None
                    if now - start_at >= 60:
                        delay = 2
                    log(f"Zigbee2MQTT is gestopt. Nieuwe poging over {delay} seconden.")
                    start_at = now + delay
                    delay = min(delay * 2, 60)
                if enabled and process is None and now >= start_at:
                    process = subprocess.Popen(  # noqa: S603 - vaste Node-runtime en toepassing
                        [str(ZIGBEE_RUNTIME / "usr/bin/node"), "index.js"],
                        cwd=ZIGBEE_JS, env=env, start_new_session=True,
                    )
                    log(f"Zigbee2MQTT gestart als gebruiker {os.geteuid()}.")
                    start_at = now
        finally:
            if process is not None:
                stop_child(process)


class Supervisor:
    def __init__(self, env: dict[str, str]):
        self.env = env
        self.children: dict[str, subprocess.Popen] = {}
        self.stopping = False
        self.new_storage = False

    def stop(self, *_args) -> None:
        self.stopping = True

    def reap_adopted(self) -> None:
        # PID 1 kan verweesde nakomelingen erven. Bewaakte kinderen houdt Popen zelf bij.
        tracked = {process.pid for process in self.children.values()}
        path = Path(f"/proc/self/task/{os.getpid()}/children")
        for child in map(int, path.read_text().split()):
            if child not in tracked:
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(child, os.WNOHANG)

    def wait(self, probe, label: str, timeout: float = 45) -> None:
        deadline = time.monotonic() + timeout
        while not self.stopping and not self.failed_child():
            if probe():
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        raise StartFout(f"{label} is niet beschikbaar.")

    def start(self, name: str, cmd: list[str]) -> subprocess.Popen:
        process = subprocess.Popen(cmd, env=self.env, start_new_session=True)  # noqa: S603 - vaste dienstcommando's
        self.children[name] = process
        return process

    def run(self, name: str, cmd: list[str]) -> int:
        process = self.start(name, cmd)
        while not self.stopping and process.poll() is None:
            time.sleep(0.1)
        if self.stopping:
            raise StartFout("Stop ontvangen tijdens het opstarten.")
        del self.children[name]
        return process.returncode

    def database(self) -> None:
        pgdata = DATA / "postgres" / "data"
        if not pgdata.exists():
            if not self.new_storage:
                raise StartFout("De bestaande database ontbreekt. Zet de bijbehorende snapshot terug.")
            with tempfile.TemporaryDirectory(dir=pgdata.parent, prefix=".init-") as temp:
                staging = Path(temp) / "data"
                command = ["initdb", "-D", str(staging), "-U", "apps", "--auth-local=peer", "--auth-host=reject",
                           "--encoding=UTF8", "--locale=C.UTF-8"]
                if self.run("initialisatie", command):
                    raise StartFout("De database kon niet worden geïnitialiseerd.")
                staging.rename(pgdata)
                sync_directory(pgdata.parent)
        self.start("postgres", ["postgres", "-D", str(pgdata), "-c", "listen_addresses=",
                               "-c", f"unix_socket_directories={PG_SOCKET}", "-c", "unix_socket_permissions=0700"])
        def database_answer(query: str, database: str = "postgres") -> bytes | None:
            test = subprocess.run(  # noqa: S603 - vaste socket en queries
                ["psql", "-XAtq", "-d", database, "-c", query],  # noqa: S607 - ingebouwde PostgreSQL-client
                env=self.env, check=False, timeout=3,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            return test.stdout.strip() if test.returncode == 0 else None
        self.wait(lambda: database_answer("SELECT 1") == b"1", "PostgreSQL")
        if not self.new_storage:
            found = database_answer("SELECT 1 FROM pg_database WHERE datname = 'shc'")
            if found != b"1":
                raise StartFout("De bestaande applicatiedatabase ontbreekt of is onleesbaar. Herstel de snapshot.")
            return
        databases = database_answer(
            "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres'",
        )
        # PostgreSQL 16 reserveert OID's onder 16384 voor systeemobjecten uit initdb.
        empty = ("SELECT NOT EXISTS (SELECT 1 FROM pg_class WHERE oid >= 16384) "
                 "AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE oid >= 16384)")
        if (databases not in (b"", b"shc") or database_answer(empty) != b"t"
                or (databases == b"shc" and database_answer(empty, "shc") != b"t")):
            raise StartFout("De eerste database-initialisatie kan niet veilig doorgaan. Controleer de snapshot.")
        if databases == b"":
            if self.run("databaseaanmaak", ["createdb", "shc"]):
                raise StartFout("De applicatiedatabase kon niet worden geopend of aangemaakt.")
        finish_first_start(DATA)
        self.new_storage = False

    def start_services(self, directory: Path, config: Path, password: str, zigbee: Zigbee | None) -> None:
        ALIVE.unlink(missing_ok=True)
        if "postgres" not in self.children:
            self.database()
        code = self.run("migratie", ["shc-api", "migrate"])
        if code not in (0, SCHEMA_NIEUWER):
            raise StartFout("Database-update geweigerd. De app start zonder geslaagde migratie niet.")
        self.start("api", ["shc-api", "serve", "--host", "127.0.0.1", "--port", "8000"])
        if code == SCHEMA_NIEUWER:
            self.wait(lambda: http_status(RECOVERY) == 200, "Herstelinformatie")
        else:
            self.wait(lambda: http_status(API_READY) == 200, "API")
            self.start("worker", ["shc-api", "worker"])
        self.start(
            "nginx", ["nginx", "-p", str(directory) + "/", "-c", str(directory / "nginx.conf"), "-g", "daemon off;"],
        )
        if code == 0:
            self.start("mosquitto", ["mosquitto", "-c", str(config)])
            self.wait(lambda: broker_accepts(password), "MQTT-broker")
            if zigbee:
                zigbee.enable(True)

    def failed_child(self) -> str | None:
        return next((name for name, proc in self.children.items() if proc.poll() is not None), None)

    def shutdown(self) -> None:
        for name, proc in reversed(self.children.items()):
            stop_child(proc, postgres=name == "postgres")
        self.children.clear()

    def watch(self, run_dir: Path, config: Path, password: str, zigbee: Zigbee | None) -> None:
        while not self.stopping:
            try:
                self.start_services(run_dir, config, password, zigbee)
                while not self.stopping:
                    failed = self.failed_child()
                    if failed:
                        log(f"{failed} is gestopt. De bewaker start de kerndiensten opnieuw.")
                        break
                    self.reap_adopted()
                    time.sleep(0.25)
            finally:
                if zigbee:
                    zigbee.enable(False)
                self.shutdown()
            if not self.stopping:
                time.sleep(2)


def service_env() -> dict[str, str]:
    # Een formulier- of Docker-variabele kan de socket of proxygrens niet omzetten.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")
           and k.upper() not in {"SHC_DATABASE_PASSWORD_FILE", "SHC_SECRET_KEY"}}
    env.update(
        HOME=str(DATA),
        PGHOST=str(PG_SOCKET),
        PGUSER="apps",
        SHC_DATABASE_URL=f"postgresql+asyncpg://apps@/shc?host={PG_SOCKET}",
        SHC_DATA_DIR=str(DATA / "app"),
        SHC_BACKUP_DIR=str(DATA / "backups"),
        SHC_ZIGBEE2MQTT_DIR=str(DATA / "zigbee2mqtt"),
        SHC_SECRET_KEY_FILE=str(DATA / "secrets" / "app-key"),
        SHC_WORKER_ALIVE_FILE=str(ALIVE),
        SHC_FORWARDED_ALLOW_IPS="127.0.0.1",
        SHC_MQTT_HOST="127.0.0.1",
        SHC_MQTT_COMPOSE_ALIAS="true",
        SHC_MQTT_PORT="1883",
        SHC_MQTT_USERNAME="shc",
        SHC_MQTT_PASSWORD_FILE=str(DATA / "secrets" / "mqtt-shc"),
    )
    return env


def main() -> int:
    os.umask(0o077)
    root = os.geteuid() == 0
    zigbee = None
    storage_fd = None
    supervisor = Supervisor(service_env())
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, supervisor.stop)
    try:
        storage_fd = os.open(DATA, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(storage_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StartFout("Deze opslag is in gebruik door een andere app-instantie.") from None
        supervisor.new_storage = check_layout(DATA)
        if root:
            transfer_ownership(DATA)
        groups = zigbee_groups(ZIGBEE, root=root)
        if groups is not None:
            zigbee = Zigbee(groups, storage_fd)
        drop_privileges()
        if not shutil.which("pg_dump"):
            raise StartFout("pg_dump ontbreekt. De database wordt niet gewijzigd.")
        password = prepare_secrets(DATA)
        if supervisor.new_storage:
            supervisor.database()
        zigbee_password = None
        if zigbee:
            try:
                zigbee_password = prepare_zigbee(DATA / "zigbee2mqtt")
            except ValueError as error:
                log(f"Zigbee2MQTT blijft uit: {error}")
            except OSError:
                log("Zigbee2MQTT blijft uit: controleer de Zigbee-opslag en de bestandsrechten.")
            if zigbee_password is None:
                zigbee.close()
                zigbee = None
        with tempfile.TemporaryDirectory(dir=DATA / "app", prefix=".run-") as run:
            run_dir = Path(run)
            prepare_web(run_dir, supervisor.env)
            config = prepare_broker(run_dir, DATA / "mosquitto", password, zigbee_password)
            supervisor.watch(run_dir, config, password, zigbee)
        return 0
    except StartFout as error:
        if not supervisor.stopping:
            log(f"Opstarten geweigerd: {error}")
        return 0 if supervisor.stopping else 1
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        if isinstance(error, ValueError):
            log(f"Opstarten geweigerd: {error}")
        else:
            log("Opstarten mislukt: controleer of /appdata schrijfbaar is voor gebruiker 568 en de rechtenwissel lukt.")
        return 1
    finally:
        if zigbee:
            zigbee.close()
        supervisor.shutdown()
        if storage_fd is not None:
            os.close(storage_fd)


if __name__ == "__main__":
    raise SystemExit(main())
