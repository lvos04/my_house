"""Unit tests for the infra scripts (python standard library only, like the scripts themselves).

    python3 -m unittest discover -s infra/tests -v

The whole stack is tested by validate_compose.sh (compose config, every TrueNAS render) and
smoke_test.py (a running stack); these cover the logic that needs no Docker.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
COMPOSE = SCRIPTS.parent / "compose" / "compose.yaml"
WEB_CONFIG_SCRIPT = SCRIPTS.parent.parent / "apps" / "web" / "docker" / "15-shc-config.sh"
sys.path.insert(0, str(SCRIPTS))

import render_truenas  # noqa: E402
import smoke_test  # noqa: E402


def render(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = render_truenas.main(["--root", "/mnt/tank/apps/smart-home", "--version", "1.2.3", *args])
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


class NtpServerOption(unittest.TestCase):
    def test_accepted_forms(self) -> None:
        for value in (
            "pool.ntp.org",
            "192.168.1.1",
            "192.168.1.1:123",
            "ntp.lan:1123",
            "router.local.",
            "fritz.box",
            "fd00::1",
            "[fd00::1]",
            "[fd00::1]:123",
        ):
            with self.subTest(value=value):
                self.assertEqual(render_truenas._ntp_server(value), value)

    def test_empty_disables(self) -> None:
        self.assertEqual(render_truenas._ntp_server(""), "")
        self.assertEqual(render_truenas._ntp_server("   "), "")

    def test_rejected_forms(self) -> None:
        for value in (
            "ntp server",
            'ntp"x',
            "$HOME",
            "${SHC_ROOT}",
            "-bad.example",
            "host:",
            "host:0",
            "host:65536",
            "host:abc",
            "[fd00::1",
            "[fd00::1]x",
            "[geen-ipv6]:123",
            "a:b:c",
            "udp://pool.ntp.org",
        ):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError) as ctx:
                render_truenas._ntp_server(value)
            self.assertIn("bijvoorbeeld", str(ctx.exception))

    def test_default_in_render(self) -> None:
        code, out, _err = render()
        self.assertEqual(code, 0)
        self.assertEqual(out.count('SHC_NTP_SERVER: "pool.ntp.org"'), 1)

    def test_custom_in_render(self) -> None:
        code, out, _err = render("--ntp-server", "192.168.1.1:123")
        self.assertEqual(code, 0)
        self.assertIn('SHC_NTP_SERVER: "192.168.1.1:123"', out)
        self.assertNotIn('SHC_NTP_SERVER: "pool.ntp.org"', out)

    def test_empty_in_render(self) -> None:
        code, out, _err = render("--ntp-server", "")
        self.assertEqual(code, 0)
        self.assertIn('SHC_NTP_SERVER: ""', out)

    def test_invalid_in_render(self) -> None:
        code, out, err = render("--ntp-server", "ntp server")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--ntp-server", err)
        self.assertIn("bijvoorbeeld 192.168.1.1", err)

    def test_compose_passes_it_to_api_and_worker(self) -> None:
        # Both api and worker use the shared x-app environment; the default must match the app's.
        text = COMPOSE.read_text(encoding="utf-8")
        env = text.split("x-app-env: &app-env", 1)[1].split("\n\n", 1)[0]
        self.assertIn('SHC_NTP_SERVER: "${SHC_NTP_SERVER:-pool.ntp.org}"', env)
        self.assertIn("environment: *app-env", text.split("x-app: &app", 1)[1].split("\n\n", 1)[0])


class Zigbee2mqttInBackup(unittest.TestCase):
    def quiet(self, rec: dict) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            smoke_test.check_z2m_in_backup(rec)
        return out.getvalue()

    def test_included(self) -> None:
        rec = {"zigbee2mqtt_included": True, "detail": {"zigbee2mqtt": {"files": 2, "included": True}}}
        self.assertIn("Zigbee2MQTT-gegevens in de back-up (2 bestanden)", self.quiet(rec))

    def test_unreadable_fails_with_reason(self) -> None:
        rec = {
            "zigbee2mqtt_included": False,
            "detail": {
                "zigbee2mqtt": {
                    "mounted": True,
                    "included": False,
                    "unreadable": ["secret.yaml"],
                    "detail": "Zigbee2MQTT-map is gekoppeld maar niet (helemaal) leesbaar voor de back-up: "
                    "secret.yaml. Geef groep 10001 leesrechten (zie installatie).",
                }
            },
        }
        with self.assertRaises(smoke_test.Fail) as ctx:
            self.quiet(rec)
        msg = str(ctx.exception)
        self.assertIn("Zigbee2MQTT-gegevens ontbreken in de back-up", msg)
        self.assertIn("Reden volgens de API: Zigbee2MQTT-map is gekoppeld maar niet", msg)
        self.assertEqual(msg.count("secret.yaml."), 1)  # named once: the API's reason already lists it
        self.assertIn("groep 10001", msg)
        self.assertNotIn("..", msg)

    def test_unreadable_listed_when_reason_does_not_name_it(self) -> None:
        rec = {
            "zigbee2mqtt_included": False,
            "detail": {"zigbee2mqtt": {"unreadable": ["coordinator_backup.json"], "detail": "Niet leesbaar."}},
        }
        with self.assertRaises(smoke_test.Fail) as ctx:
            self.quiet(rec)
        self.assertIn("Niet leesbaar: coordinator_backup.json.", str(ctx.exception))

    def test_not_mounted_fails(self) -> None:
        rec = {"zigbee2mqtt_included": False, "detail": {"zigbee2mqtt": {"detail": "Geen Zigbee2MQTT-map gekoppeld."}}}
        with self.assertRaises(smoke_test.Fail) as ctx:
            self.quiet(rec)
        self.assertIn("Geen Zigbee2MQTT-map gekoppeld", str(ctx.exception))

    def test_missing_field_fails(self) -> None:
        # An API that does not report it at all must not pass silently.
        for rec in ({}, {"zigbee2mqtt_included": None, "detail": None}, {"zigbee2mqtt_included": "true"}):
            with self.subTest(rec=rec), self.assertRaises(smoke_test.Fail) as ctx:
                self.quiet(rec)
            self.assertIn("geen uitleg van de API", str(ctx.exception))


@unittest.skipUnless(shutil.which("sh"), "geen sh")
class WebConfigScript(unittest.TestCase):
    """apps/web/docker/15-shc-config.sh: HUB_URL -> /_shc/config.json for "Terug naar de Hub" (O-11)."""

    def config(self, hub: str | None) -> tuple[dict, str]:
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k != "HUB_URL"}
            env["SHC_CONFIG_DIR"] = d
            if hub is not None:
                env["HUB_URL"] = hub
            proc = subprocess.run(["sh", str(WEB_CONFIG_SCRIPT)], env=env, capture_output=True, text=True, check=False)  # noqa: S603, S607
            self.assertEqual(proc.returncode, 0, proc.stderr)  # an invalid value never stops the container
            return json.loads(Path(d, "config.json").read_text()), proc.stderr

    def test_valid_addresses_become_an_origin(self) -> None:
        for hub, origin in (
            ("https://hub.thuis:8443", "https://hub.thuis:8443"),
            ("https://hub.thuis:8443/", "https://hub.thuis:8443"),
            ("http://192.168.1.5", "http://192.168.1.5"),
            ("https://[2001:db8::1]:8443", "https://[2001:db8::1]:8443"),
            ("https://[1::]", "https://[1::]"),
            ("https://[::10]", "https://[::10]"),
            ("http://10.0.0.1:8088", "http://10.0.0.1:8088"),
            ("https://hub-1.thuis", "https://hub-1.thuis"),
        ):
            with self.subTest(hub=hub):
                self.assertEqual(self.config(hub)[0], {"hub_url": origin})

    def test_empty_or_absent_is_no_link(self) -> None:
        self.assertEqual(self.config(None), ({}, ""))
        self.assertEqual(self.config(""), ({}, ""))

    def test_invalid_addresses_are_ignored(self) -> None:
        for hub in (
            "http://127.0.0.1:8443",
            "http://localhost",
            "http://hub.localhost",
            "http://0.0.0.0",
            "http://169.254.10.1",
            "https://[::1]",
            "https://[fe80::1]",
            # other spellings of the same loopback/unspecified addresses (the browser normalises them)
            "http://0177.0.0.1:8443",
            "http://2130706433",
            "http://0x7f.1",
            "http://127.1",
            "http://1.2.3",
            "http://169.254.0x1.1",
            "http://010.0.0.1",
            "https://[0::1]",
            "https://[::0001]",
            "https://[0:0:0:0:0:0:0:1]",
            "https://[0::]",
            "https://[::ffff:127.0.0.1]",
            "https://[0:0:0:0:0:ffff:7f00:1]",
            "https://user:pass@hub.thuis",
            "https://hub.thuis/pad",
            "https://hub.thuis?x=1",
            "https://hub.thuis#x",
            'https://hub"thuis',
            "https://hub.thuis:0",
            "https://hub.thuis:70000",
            "ftp://hub.thuis",
            "javascript:alert(1)",
            "hub.thuis",
        ):
            with self.subTest(hub=hub):
                cfg, err = self.config(hub)
                self.assertEqual(cfg, {})
                self.assertIn("HUB_URL genegeerd", err)


if __name__ == "__main__":
    unittest.main()
