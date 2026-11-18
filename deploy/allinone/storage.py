"""Ingebruikname van appdata met behoud van bestaande waarden."""

import os
import re
import secrets
import stat
import subprocess
import tempfile
from pathlib import Path

from .runtime import GID, UID

ASSETS = Path("/opt/shc")
ZIGBEE = Path("/dev/zigbee")
ADAPTERS = {"ember", "zstack", "deconz", "zigate", "zboss"}
PASSWORD = re.compile(r"[A-Za-z0-9_-]{32,128}")


def transfer_ownership(data: Path) -> None:
    # Doorloop uitsluitend deze opslag, zonder symlinks of harde links over te nemen.
    if not stat.S_ISDIR(data.lstat().st_mode):
        raise ValueError("De opslag moet een map zijn.")
    device = data.stat().st_dev
    for _, dirs, files, fd in os.fwalk(data, onerror=_raise):
        info = os.fstat(fd)
        if (info.st_uid, info.st_gid) != (UID, GID):
            os.fchown(fd, UID, GID)
        for name in dirs[:]:
            entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(entry.st_mode) or entry.st_dev != device:
                dirs.remove(name)
        for name in files:
            entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1:
                if (entry.st_uid, entry.st_gid) != (UID, GID):
                    os.chown(name, UID, GID, dir_fd=fd, follow_symlinks=False)


def _raise(error: OSError) -> None:
    raise error


def directory(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError(f"De opslagmap {path.name} is geen gewone map.")
    path.chmod(0o700)


def create_once(path: Path, content: bytes) -> None:
    if os.path.lexists(path):
        return
    # De uiteindelijke naam wordt pas zichtbaar als de inhoud is weggeschreven.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".nieuw-") as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
        try:
            os.link(out.name, path)
        except FileExistsError:
            return
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def read_private(path: Path, limit: int = 4096) -> str:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError(f"De bestandsrechten van {path.name} zijn ongeldig.")
        try:
            raw = source.read(limit + 1)
        except UnicodeError:
            raw = ""
    value = raw.strip()
    if not value or len(raw) > limit:
        raise ValueError(f"De inhoud van {path.name} is ongeldig.")
    return value


def password(path: Path) -> str:
    value = read_private(path)
    if not PASSWORD.fullmatch(value):
        raise ValueError(f"De inhoud van {path.name} is ongeldig.")
    return value


def prepare_secrets(data: Path) -> str:
    for part in ("postgres", "app", "backups", "secrets", "mosquitto"):
        directory(data / part)
    for name in ("app-key", "mqtt-shc"):
        create_once(data / "secrets" / name, (secrets.token_urlsafe(48) + "\n").encode())
        password(data / "secrets" / name)
    return password(data / "secrets" / "mqtt-shc")


def prepare_zigbee(path: Path) -> str:
    adapter = os.environ.get("SHC_ZIGBEE_ADAPTER", "ember")
    if adapter not in ADAPTERS:
        raise ValueError("SHC_ZIGBEE_ADAPTER is ongeldig. Kies ember, zstack, deconz, zigate of zboss.")
    directory(path)
    config = path / "configuration.yaml"
    secret = path / "secret.yaml"
    template = (ASSETS / "zigbee2mqtt.yaml.template").read_text()
    initial = template.replace("mqtt://mosquitto:1883", "mqtt://127.0.0.1:1883").replace("__ADAPTER__", adapter)
    create_once(config, initial.encode())
    create_once(secret, f"mqtt_password: '{secrets.token_urlsafe(48)}'\n".encode())
    if not isinstance(read_yaml(config, 1024 * 1024), dict):
        raise ValueError("De inhoud van configuration.yaml is ongeldig.")
    values = read_yaml(secret, 4096)
    password = values.get("mqtt_password") if isinstance(values, dict) else None
    if not isinstance(password, str) or not PASSWORD.fullmatch(password):
        raise ValueError("De inhoud van secret.yaml is ongeldig.")
    return password


def read_yaml(path: Path, limit: int):
    import yaml

    try:
        return yaml.safe_load(read_private(path, limit))
    except yaml.YAMLError:
        raise ValueError(f"De inhoud van {path.name} is ongeldig.") from None


def prepare_broker(run_dir: Path, mqtt_dir: Path, app_password: str, zigbee_password: str | None) -> Path:
    config = run_dir / "mosquitto.conf"
    passwords = run_dir / "mqtt-users"
    acl = run_dir / "mqtt-acl"
    create_once(acl, (ASSETS / "acl").read_bytes())
    contents = f"shc:{app_password}\n"
    if zigbee_password:
        contents += f"zigbee2mqtt:{zigbee_password}\n"
    create_once(passwords, contents.encode())
    subprocess.run(  # noqa: S603 - vast programma en afgeschermd lokaal bestand
        ["/usr/bin/mosquitto_passwd", "-U", str(passwords)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    passwords.chmod(0o600)
    config.write_text(
        "listener 1883 127.0.0.1\nallow_anonymous false\n"
        f"password_file {passwords}\nacl_file {acl}\n"
        f"persistence true\npersistence_location {mqtt_dir}/\nlog_dest stderr\n"
    )
    return config


def prepare_web(run_dir: Path, env: dict[str, str]) -> None:
    (run_dir / "shc-config").mkdir()
    subprocess.run(  # noqa: S603 - bestaand vast configscript
        ["/bin/sh", str(ASSETS / "15-shc-config.sh")],
        env={**env, "SHC_CONFIG_DIR": str(run_dir / "shc-config")},
        check=True,
    )
    # Gebruik de bestaande proxy-, cache- en veiligheidsregels, met een vaste upstream
    # en zonder vertrouwen in Cloudflare of in doorgestuurde headers van de cliënt.
    template = (ASSETS / "nginx.conf.template").read_text()
    excluded = (
        '"${TUNNEL_IP}:https"', "set_real_ip_from ", "real_ip_header ",
        "resolver ", "resolver_timeout ",
    )
    web = "\n".join(line for line in template.splitlines() if not line.lstrip().startswith(excluded))
    web = web.replace("${API_UPSTREAM}", "127.0.0.1:8000")
    web = web.replace(
        'proxy_set_header Forwarded "";',
        'proxy_set_header Forwarded "";\n        proxy_set_header X-Forwarded-Host "";',
    )
    web = web.replace("/tmp/shc-config/config.json", str(run_dir / "shc-config/config.json"))  # noqa: S108 - sjabloon
    web = web.replace(
        "    location = /healthz {",
        "    location = /readyz { proxy_pass $api_upstream; }\n\n    location = /healthz {",
    )
    (run_dir / "web.conf").write_text(web)
    (run_dir / "nginx.conf").write_bytes((ASSETS / "nginx.conf").read_bytes())
