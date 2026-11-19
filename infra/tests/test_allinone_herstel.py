"""Startweigeringen en gegevensbehoud bij een tweede start van de Custom App."""

import base64
import contextlib
import hashlib
import io
import os
import secrets
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from deploy.allinone import storage, supervisor

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "infra/compose/config"


class OpslagHerstel(unittest.TestCase):
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
