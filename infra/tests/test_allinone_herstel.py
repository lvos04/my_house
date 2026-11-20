"""Startweigeringen en gegevensbehoud bij een tweede start van de Custom App."""

import base64
import contextlib
import hashlib
import io
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from deploy.allinone import storage, supervisor

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "infra/compose/config"


class OpslagHerstel(unittest.TestCase):
    def test_gepauzeerde_zigbee_opslag_blijft_na_start_dicht(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zigbee2mqtt"
            path.mkdir(mode=0o500)
            with self.assertRaisesRegex(ValueError, "gepauzeerd"):
                storage.prepare_zigbee(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o500)
            self.assertEqual(list(path.iterdir()), [])

    def test_opslaggrens_voor_publicatie(self):
        self.controleer_opslaggrens("voor-link")

    def test_opslaggrens_na_mapsynchronisatie(self):
        self.controleer_opslaggrens("na-mapsync")

    def controleer_opslaggrens(self, phase):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            stopped = subprocess.run(  # noqa: S603 - vaste proefcode in een eigen subprocess
                [sys.executable, "-B", "-c", """
import os
import sys
from pathlib import Path
from deploy.allinone import storage

phase, directory = sys.argv[1:]
data = Path(directory)
sync = storage.sync_directory

def interrupt_link(source, target):
    os._exit(75)

def interrupt_sync(path):
    sync(path)
    if path == data / "secrets" and (path / "app-key").is_file():
        os._exit(75)

if phase == "voor-link":
    storage.os.link = interrupt_link
else:
    storage.sync_directory = interrupt_sync
storage.prepare_secrets(data)
""", phase, d],
                cwd=ROOT, capture_output=True, check=False,
            )
            self.assertEqual(stopped.returncode, 75, stopped.stderr.decode())
            secret_dir = data / "secrets"
            self.assertFalse(
                any(p.is_file() and p.parent != secret_dir for p in data.rglob("*")),
                f"{phase}: sleutelbestand buiten secrets",
            )
            self.assertEqual(stat.S_IMODE(secret_dir.stat().st_mode), 0o700)
            key = secret_dir / "app-key"
            self.assertEqual(key.exists(), phase == "na-mapsync")
            self.assertFalse((secret_dir / "mqtt-shc").exists())
            self.assertFalse(any((data / "postgres").iterdir()))
            (pending,) = (p for p in secret_dir.iterdir() if p != key)
            self.assertRegex(pending.name, r"^\.nieuw-app-key-.+$")
            info = pending.stat()
            self.assertEqual((info.st_uid, info.st_gid), (os.geteuid(), os.getegid()))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            storage.password(pending)
            before = (key.read_bytes(), key.stat().st_ino) if key.exists() else None
            if before is not None:
                self.assertTrue(pending.samefile(key))
                self.assertEqual(info.st_nlink, 2)

            self.assertTrue(storage.check_layout(data))
            storage.prepare_secrets(data)
            self.assertEqual({p.name for p in secret_dir.iterdir()}, set(storage.CORE_SECRETS))
            self.assertEqual(key.stat().st_nlink, 1)
            if before is not None:
                self.assertEqual((key.read_bytes(), key.stat().st_ino), before)

    def test_harde_stop_tijdens_sleutelopslag_hervat_voor_initdb(self):
        for phase in ("voor-link", "na-link", "oude-schrijfmethode", "oude-startmarkering"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as d:
                data = Path(d)
                stopped = subprocess.run(  # noqa: S603 - vaste proefcode in een eigen subprocess
                    [sys.executable, "-B", "-c", """
import os
import sys
from pathlib import Path
from deploy.allinone import storage

phase, data = sys.argv[1:]
link = os.link
temporary_file = storage.tempfile.NamedTemporaryFile

def interrupt(source, target):
    if phase != "voor-link":
        link(source, target)
    os._exit(75)

def legacy_file(*, dir, prefix):
    if phase == "oude-startmarkering":
        return temporary_file(dir=Path(data) / storage.FIRST_START, prefix=".nieuw-app-key-")
    return temporary_file(dir=Path(data) / "secrets", prefix=".nieuw-")

if phase in ("oude-startmarkering", "oude-schrijfmethode"):
    storage.tempfile.NamedTemporaryFile = legacy_file
storage.os.link = interrupt
storage.prepare_secrets(Path(data))
""", phase, d],
                    cwd=ROOT, capture_output=True, check=False,
                )
                self.assertEqual(stopped.returncode, 75, stopped.stderr.decode())
                self.assertFalse(any((data / "postgres").iterdir()))
                key = data / "secrets/app-key"
                before = (key.read_bytes(), key.stat().st_ino) if key.exists() else None
                self.assertEqual(before is None, phase == "voor-link")

                self.assertTrue(storage.check_layout(data))
                storage.prepare_secrets(data)
                if before is not None:
                    self.assertEqual((key.read_bytes(), key.stat().st_ino), before)
                self.assertEqual(key.stat().st_nlink, 1)
                self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
                self.assertEqual({p.name for p in (data / "secrets").iterdir()}, set(storage.CORE_SECRETS))
                self.assertFalse(any((data / storage.FIRST_START).iterdir()))

    def test_restbestand_wordt_bij_onbekende_of_gebruikte_opslag_behouden(self):
        for scenario in ("onbekend", "externe-link", "extra-link", "gegevens", "ongeldige-sleutel", "verkeerde-naam"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as d:
                data = Path(d) / "appdata"
                data.mkdir()
                storage.prepare_secrets(data)
                key = data / "secrets/app-key"
                filename = ".nieuw-mqtt-shc-proef" if scenario == "verkeerde-naam" else ".nieuw-proef"
                rest = key.parent / filename
                if scenario == "onbekend":
                    storage.create_once(rest, b"onbekende proefgegevens")
                elif scenario == "externe-link":
                    source = Path(d) / "proefbestand"
                    storage.create_once(source, b"bestaande proefgegevens")
                    os.link(source, rest)
                else:
                    os.link(key, rest)
                    if scenario == "extra-link":
                        os.link(key, Path(d) / "proefbestand")
                    elif scenario == "gegevens":
                        (data / "postgres/data").mkdir()
                        (data / "postgres/data/PG_VERSION").write_text("16\n")
                    else:
                        key.write_bytes(b"")
                before = (rest.read_bytes(), rest.stat().st_nlink, key.read_bytes())
                with self.assertRaisesRegex(ValueError, "snapshot|ongeldig"):
                    storage.prepare_secrets(data)
                self.assertEqual((rest.read_bytes(), rest.stat().st_nlink, key.read_bytes()), before)

    def test_eerste_start_voor_initdb_hervat_met_dezelfde_geheimen(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            storage.prepare_secrets(data)
            secrets_before = {p.name: p.read_bytes() for p in (data / "secrets").iterdir()}

            self.assertTrue(storage.check_layout(data))
            storage.prepare_secrets(data)
            self.assertEqual({p.name: p.read_bytes() for p in (data / "secrets").iterdir()}, secrets_before)

    def test_hervatten_weigert_bestaande_gegevens_zonder_klare_database(self):
        for name in ("postgres/data/PGDATA", "app/proef", "backups/proef", "mosquitto/proef", "zigbee2mqtt/proef"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                data = Path(d)
                storage.prepare_secrets(data)
                existing = data / name
                existing.parent.mkdir(parents=True, exist_ok=True)
                existing.write_bytes(b"bestaande proefgegevens")
                with self.assertRaisesRegex(ValueError, "snapshot"):
                    storage.prepare_secrets(data)
                self.assertEqual(existing.read_bytes(), b"bestaande proefgegevens")

    def test_bestaande_database_wordt_bij_verdwijnen_niet_opnieuw_aangemaakt(self):
        with tempfile.TemporaryDirectory() as d, patch.object(supervisor, "DATA", Path(d)):
            (Path(d) / "postgres").mkdir()
            control = supervisor.Supervisor({})
            with patch.object(control, "run") as run, self.assertRaisesRegex(supervisor.StartFout, "snapshot"):
                control.database()
            run.assert_not_called()

    def test_ontbrekend_geheim_bij_gevulde_opslag(self):
        for name in ("app-key", "mqtt-shc"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                data = Path(d)
                storage.prepare_secrets(data)
                db = data / "postgres/data"
                db.mkdir()
                (db / "PG_VERSION").write_text("16\n")
                key = data / "secrets" / name
                original = key.read_bytes()
                key.unlink()
                with self.assertRaisesRegex(ValueError, "herstel|terug"):
                    storage.prepare_secrets(data)
                self.assertFalse(key.exists())
                storage.create_once(key, original)
                storage.prepare_secrets(data)
                self.assertEqual(key.read_bytes(), original)

    def test_compose_wordt_voor_eigendomsoverdracht_geweigerd(self):
        for marker in ("postgres/PG_VERSION", "secrets/secret_key", "onbekende-inhoud"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as d:
                data = Path(d)
                old = data / marker
                old.parent.mkdir(exist_ok=True)
                old.write_bytes(os.urandom(32))
                before = (old.stat(), old.read_bytes())
                with (
                    patch.object(supervisor, "DATA", data),
                    patch.object(os, "geteuid", return_value=0),
                    patch.object(os, "umask"),
                    patch.object(supervisor, "transfer_ownership", side_effect=AssertionError("te vroege chown")),
                    patch.object(supervisor.signal, "signal"),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(supervisor.main(), 1)
                self.assertEqual((old.stat().st_uid, old.stat().st_gid, old.read_bytes()),
                                 (before[0].st_uid, before[0].st_gid, before[1]))
                self.assertFalse((data / "postgres/data").exists())

    def test_onvolledig_zigbeenetwork_blijft_behouden(self):
        with tempfile.TemporaryDirectory() as d, patch.object(storage, "ASSETS", ASSETS):
            path = Path(d) / "zigbee"
            storage.prepare_zigbee(path)
            for name in ("configuration.yaml", "secret.yaml"):
                target = path / name
                original = target.read_bytes()
                target.unlink()
                with self.assertRaisesRegex(ValueError, "herstel|terug"):
                    storage.prepare_zigbee(path)
                self.assertFalse(target.exists())
                storage.create_once(target, original)

    def test_onvolledige_zigbee_opslag_weigert_alleen_de_optionele_dienst(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            for name in ("app-key", "mqtt-shc"):
                (data / "secrets").mkdir(exist_ok=True)
                storage.create_once(data / "secrets" / name, secrets.token_urlsafe(48).encode())
            cluster = data / "postgres/data"
            cluster.mkdir(parents=True)
            (cluster / "PG_VERSION").write_text("16\n")
            zigbee = data / "zigbee2mqtt"
            zigbee.mkdir()
            surviving = zigbee / "configuration.yaml"
            storage.create_once(surviving, b"proef: behouden\n")
            before = surviving.read_bytes()
            with (
                patch.object(supervisor, "DATA", data),
                patch.object(os, "umask"),
                patch.object(supervisor, "transfer_ownership"),
                patch.object(supervisor, "drop_privileges"),
                patch.object(supervisor, "zigbee_groups", return_value=()),
                patch.object(supervisor, "Zigbee") as start_zigbee,
                patch.object(supervisor.signal, "signal"),
                patch.object(supervisor.shutil, "which", return_value="pg_dump"),
                patch.object(supervisor, "prepare_web"),
                patch.object(storage, "ASSETS", ASSETS),
                patch.object(supervisor.Supervisor, "watch") as watch,
                contextlib.redirect_stderr(io.StringIO()) as log,
            ):
                self.assertEqual(supervisor.main(), 0)
                self.assertIsNone(watch.call_args.args[-1])
                start_zigbee.return_value.close.assert_called_once()
                start_zigbee.return_value.enable.assert_not_called()
            self.assertIn("Zigbee2MQTT blijft uit", log.getvalue())
            self.assertIn("onvolledig", log.getvalue())
            self.assertEqual(surviving.read_bytes(), before)
            self.assertFalse((zigbee / "secret.yaml").exists())

    def test_broker_schrijft_alleen_hashes_ook_bij_onderbreking(self):
        with tempfile.TemporaryDirectory() as d, patch.object(storage, "ASSETS", ASSETS):
            path = Path(d)
            passwords = {name: secrets.token_urlsafe(36) for name in ("shc", "zigbee2mqtt")}
            original = storage.create_once
            def write_and_interrupt(target, contents):
                original(target, contents)
                if target.name == "mqtt-users":
                    raise InterruptedError
            with (
                patch.object(storage, "create_once", side_effect=write_and_interrupt),
                self.assertRaises(InterruptedError),
            ):
                storage.prepare_broker(path, path, *passwords.values())
            for target in path.iterdir():
                self.assertFalse(any(p.encode() in target.read_bytes() for p in passwords.values()))
            for line in (path / "mqtt-users").read_text().splitlines():
                user, hashed = line.split(":")
                _, algorithm, rounds, salt, digest = hashed.split("$")
                self.assertEqual(algorithm, "7")
                self.assertEqual(base64.b64decode(digest), hashlib.pbkdf2_hmac(
                    "sha512", passwords[user].encode(), base64.b64decode(salt), int(rounds)))


class ZigbeeRechten(unittest.TestCase):
    def test_direct_568_weigert_zigbee_met_gevulde_bounding_set(self):
        device = Mock(spec=Path)
        device.lstat.return_value.st_mode = stat.S_IFCHR | 0o666
        with (
            patch.object(os, "access", return_value=True),
            patch("deploy.allinone.runtime.identity", return_value={"CapBnd": "00000001"}),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertIsNone(supervisor.zigbee_groups(device, root=False))

    def test_zigbeetak_erft_geen_appvariabelen(self):
        with patch.dict(os.environ, SHC_SECRET_KEY=secrets.token_urlsafe(), PGPASSWORD=secrets.token_urlsafe()):
            r, w = os.pipe()
            try:
                os.write(w, b"+")
                with patch.object(supervisor.subprocess, "Popen", side_effect=InterruptedError) as start:
                    with self.assertRaises(InterruptedError):
                        supervisor.Zigbee.watch(r)
                env = start.call_args.kwargs["env"]
                self.assertTrue({"SHC_SECRET_KEY", "PGPASSWORD"}.isdisjoint(env))
            finally:
                os.close(r)
                os.close(w)
