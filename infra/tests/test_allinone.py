"""Negatieve en positieve controles van de Custom App-ingangen."""

import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deploy.allinone import healthcheck, storage, supervisor


class HealthcheckTest(unittest.TestCase):
    def test_mislukte_wissel_stopt_voor_alle_proeven(self):
        with (
            patch.object(healthcheck, "drop_privileges", side_effect=OSError),
            patch.object(healthcheck, "healthy") as healthy,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(healthcheck.main([]), 1)
        healthy.assert_not_called()

    def test_gebruikt_nginx_api_en_verse_workerhartslag(self):
        with tempfile.TemporaryDirectory() as directory:
            alive = Path(directory) / "alive"
            alive.touch()
            with patch.object(healthcheck, "http_status", return_value=200) as probe:
                self.assertTrue(healthcheck.healthy(alive))
                self.assertEqual(probe.call_count, 2)
                os.utime(alive, (1, 1))
                self.assertFalse(healthcheck.healthy(alive))


class OpslagTest(unittest.TestCase):
    def test_geheim_behoudt_bytes_na_herstart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app-key"
            storage.create_once(path, b"eerste inhoud")
            storage.create_once(path, b"andere inhoud")
            self.assertEqual(path.read_bytes(), b"eerste inhoud")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_lege_bestaande_inhoud_wordt_geweigerd_en_behouden(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app-key"
            storage.create_once(path, b"")
            with self.assertRaisesRegex(ValueError, "ongeldig"):
                storage.password(path)
            self.assertEqual(path.read_bytes(), b"")


class ZigbeeTest(unittest.TestCase):
    def test_geen_tekenapparaat_is_stil(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as log:
            path = Path(directory) / "zigbee"
            self.assertIsNone(supervisor.zigbee_groups(path, root=True))
            path.touch()
            self.assertIsNone(supervisor.zigbee_groups(path, root=True))
            self.assertEqual(log.getvalue(), "")

    def test_extra_apparaatgroep_alleen_bij_rootstart_en_geen_groep_nul(self):
        path = Mock(spec=Path)
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_uid=0, st_gid=27)
        with contextlib.redirect_stderr(io.StringIO()), patch.object(supervisor.os, "access", return_value=False):
            self.assertEqual(supervisor.zigbee_groups(path, root=True), (27,))
            self.assertIsNone(supervisor.zigbee_groups(path, root=False))
            path.lstat.return_value.st_gid = 0
            self.assertIsNone(supervisor.zigbee_groups(path, root=True))

    def test_ongeldige_adapter_stopt_zonder_invoer_te_loggen(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SHC_ZIGBEE_ADAPTER="ongeldig"):
            with self.assertRaisesRegex(ValueError, "SHC_ZIGBEE_ADAPTER"):
                storage.prepare_zigbee(Path(directory))

    def test_broker_antwoordt_voor_zigbee_start(self):
        control = supervisor.Supervisor({})
        zigbee = Mock()
        with (
            patch.object(control, "database"),
            patch.object(control, "run", return_value=0),
            patch.object(control, "start") as start,
            patch.object(supervisor, "http_status", return_value=200),
            patch.object(supervisor, "ALIVE"),
            patch.object(supervisor, "broker_accepts", side_effect=lambda _: self.assertFalse(zigbee.enable.called)
                         or True),
        ):
            control.start_services(Path("nginx"), Path("mqtt"), "testwaarde", zigbee)
        self.assertEqual([c.args[0] for c in start.call_args_list], ["api", "worker", "nginx", "mosquitto"])
        zigbee.enable.assert_called_once_with(True)

    def test_nieuwer_schema_start_alleen_api_en_proxy(self):
        control = supervisor.Supervisor({})
        zigbee = Mock()
        with (
            patch.object(control, "database"),
            patch.object(control, "run", return_value=supervisor.SCHEMA_NIEUWER),
            patch.object(control, "start") as start,
            patch.object(supervisor, "ALIVE"),
            patch.object(supervisor, "http_status", return_value=200) as http,
        ):
            control.start_services(Path("nginx"), Path("mqtt"), "testwaarde", zigbee)
        http.assert_called_once_with(supervisor.RECOVERY)
        self.assertEqual([c.args[0] for c in start.call_args_list], ["api", "nginx"])
        zigbee.enable.assert_not_called()
