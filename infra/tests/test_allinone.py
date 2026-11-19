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

ASSETS = Path(__file__).resolve().parents[2] / "infra/compose/config"


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
    def existing_storage(self, data):
        storage.prepare_secrets(data)
        cluster = data / "postgres/data"
        cluster.mkdir()
        (cluster / "PG_VERSION").write_text("16\n")
        storage.finish_first_start(data)

    def start_proef(self, data, device, *, uid=0):
        system = SimpleNamespace(**{**vars(os), "geteuid": lambda: uid, "umask": Mock()})
        with (
            patch.object(supervisor, "DATA", data),
            patch.object(supervisor, "ZIGBEE", device),
            patch.object(supervisor, "os", system),
            patch.object(supervisor, "transfer_ownership"),
            patch.object(supervisor, "drop_privileges") as drop,
            patch.object(supervisor, "Zigbee") as zigbee,
            patch.object(supervisor.signal, "signal"),
            patch.object(supervisor.shutil, "which", return_value="pg_dump"),
            patch.object(supervisor, "prepare_web"),
            patch.object(storage, "ASSETS", ASSETS),
            patch.object(supervisor.Supervisor, "watch") as watch,
            contextlib.redirect_stderr(io.StringIO()) as log,
        ):
            self.assertEqual(supervisor.main(), 0, log.getvalue())
            drop.assert_called_once_with()
            self.assertIs(watch.call_args.args[-1], zigbee.return_value if zigbee.called else None)
            return zigbee.call_args.args[0] if zigbee.called else None, log.getvalue()

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

    def test_uit_en_zonder_leesrecht_starten_geen_zigbee_en_blijven_gezond(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            self.existing_storage(data)
            alive = data / "app/worker-alive"
            alive.touch()
            actual_health = healthcheck.healthy
            device = Mock(spec=Path)
            device.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_uid=0, st_gid=0)
            with (
                patch.object(supervisor, "zigbee_bounding_set_empty", return_value=True),
                patch.object(os, "access", return_value=False),
                patch.object(healthcheck, "drop_privileges"),
                patch.object(healthcheck, "healthy", side_effect=lambda: actual_health(alive)),
                patch.object(healthcheck, "http_status", return_value=200),
            ):
                for path, uid in ((data / "afwezig", 0), (device, 0), (device, 568)):
                    with self.subTest(path=path, uid=uid):
                        groups, log = self.start_proef(data, path, uid=uid)
                        self.assertIsNone(groups)
                        self.assertFalse((data / "zigbee2mqtt").exists())
                        self.assertEqual(healthcheck.main([]), 0)
                        if path is device:
                            self.assertIn("kan het apparaat niet lezen en schrijven", log)
                        else:
                            self.assertEqual(log, "")

    def test_zigbee_configuratie_en_geheim_blijven_behouden_na_tweede_start(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            self.existing_storage(data)
            device = Mock(spec=Path)
            device.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_uid=0, st_gid=27)
            path = data / "zigbee2mqtt"
            with patch.dict(os.environ, SHC_ZIGBEE_ADAPTER="ember"):
                self.assertEqual(self.start_proef(data, device), ((27,), ""))
            config = path / "configuration.yaml"
            network_key = b"[" + b", ".join([b"1"] * 16) + b"]"
            config.write_bytes(config.read_bytes().replace(b"network_key: GENERATE", b"network_key: " + network_key))
            before = {file.name: file.read_bytes() for file in path.iterdir()}
            with patch.dict(os.environ, SHC_ZIGBEE_ADAPTER="zstack"):
                self.assertEqual(self.start_proef(data, device), ((27,), ""))
            self.assertEqual({file.name: file.read_bytes() for file in path.iterdir()}, before)

    def test_crashende_zigbee_wacht_steeds_langer_en_healthcheck_blijft_groen(self):
        with tempfile.TemporaryDirectory() as directory:
            alive = Path(directory) / "alive"
            alive.touch()
            device = Mock(spec=Path)
            device.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFCHR | 0o666, st_uid=0, st_gid=0)
            self.assertEqual(supervisor.zigbee_groups(device, root=True), ())
            times = iter((0, 0.25, 2, 2.25, 2.5, 6, 6.5, 7, 14, 14.75, 15, 16))
            launches = []
            now = 0
            actual_health = healthcheck.healthy

            def clock():
                nonlocal now
                now = next(times)
                return now

            def start(*args, **kwargs):
                launches.append(now)
                return Mock(poll=Mock(return_value=1))

            with (
                patch.object(supervisor, "ZIGBEE", device),
                patch.object(os, "geteuid", return_value=568),
                patch.object(supervisor.time, "monotonic", side_effect=clock),
                patch.object(supervisor.select, "select", side_effect=[([], [], [])] * 12 + [([9], [], [])]),
                patch.object(os, "read", return_value=b""),
                patch.object(supervisor.subprocess, "Popen", side_effect=start),
                patch.object(supervisor, "stop_child"),
                patch.object(healthcheck, "drop_privileges"),
                patch.object(healthcheck, "healthy", side_effect=lambda: actual_health(alive)),
                patch.object(healthcheck, "http_status", return_value=200),
                contextlib.redirect_stderr(io.StringIO()) as log,
            ):
                supervisor.Zigbee.watch(9, enabled=True)
                self.assertEqual(healthcheck.main([]), 0)
            self.assertEqual(launches, [0, 2.25, 6.5, 15])
            self.assertEqual(log.getvalue().count("gestart als gebruiker 568"), 4)
            for delay in (2, 4, 8, 16):
                self.assertIn(f"Nieuwe poging over {delay} seconden.", log.getvalue())

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
