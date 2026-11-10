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


# HUB_URL for "Terug naar de Hub" (contract 1.3 §3.2 and §2.3 rule 5). Shared by the shell script test below and,
# as a fixture file, by the web unit test (apps/web/src/test/peil-toevoegingen.test.tsx reads the same lists), so the
# script in the image and the second check in the browser follow exactly the same cases.
HUB_URL_CASES = SCRIPTS.parent / "tests" / "hub_url_gevallen.json"


def _hub_url_cases() -> dict[str, list]:
    return json.loads(HUB_URL_CASES.read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("sh"), "geen sh")
class WebConfigScript(unittest.TestCase):
    """apps/web/docker/15-shc-config.sh: HUB_URL -> /_shc/config.json for "Terug naar de Hub" (O-11).

    Runs with the system sh/awk (dash + mawk on Ubuntu) and, when busybox is available, once more with busybox sh and
    busybox awk: that is what the nginx (Alpine) image runs.
    """

    shell: tuple[str, ...] = ("sh",)
    awk_dir: str | None = None

    def config(self, hub: str | None) -> tuple[dict, str]:
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k != "HUB_URL"}
            env["SHC_CONFIG_DIR"] = d
            if self.awk_dir:
                env["PATH"] = f"{self.awk_dir}{os.pathsep}{env.get('PATH', '')}"
            if hub is not None:
                env["HUB_URL"] = hub
            proc = subprocess.run(  # noqa: S603
                [*self.shell, str(WEB_CONFIG_SCRIPT)], env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)  # an invalid value never stops the container
            return json.loads(Path(d, "config.json").read_text()), proc.stderr

    def test_valid_addresses_become_an_origin(self) -> None:
        for hub, origin in _hub_url_cases()["geldig"]:
            with self.subTest(hub=hub):
                self.assertEqual(self.config(hub), ({"hub_url": origin}, ""))

    def test_empty_or_absent_is_no_link(self) -> None:
        self.assertEqual(self.config(None), ({}, ""))
        self.assertEqual(self.config(""), ({}, ""))

    def test_invalid_addresses_are_ignored(self) -> None:
        for hub in _hub_url_cases()["ongeldig"]:
            with self.subTest(hub=hub):
                cfg, err = self.config(hub)
                self.assertEqual(cfg, {})
                self.assertIn("HUB_URL genegeerd", err)

    def test_the_file_is_always_valid_json_even_for_hostile_values(self) -> None:
        for hub in ('https://a.b\n","x":"y', 'https://a.b"}', "https://a.b\\", "https://a.b\thuis", "https://a.b\u00e9"):
            with self.subTest(hub=hub):
                self.assertEqual(self.config(hub)[0], {})

    def test_cases_cover_every_rule_of_section_3_2(self) -> None:
        cases = _hub_url_cases()
        ongeldig = set(cases["ongeldig"])
        # the examples the contract names literally (§3.2 "Adresregels (vorm)")
        for voorbeeld in ("http://127.1", "http://2130706433", "http://0x7f000001", "http://010.0.0.1", "http://0",
                          "http://localhost.", "https://[::]", "http://0.0.0.0", "http://169.254.1.1",
                          "https://[fe80::1]", "https://hub.thuis/pad", "https://u:p@hub.thuis", "https://hub.thuis?x",
                          "https://hub.thuis#x", "https://::1", "ftp://hub.thuis"):
            self.assertIn(voorbeeld, ongeldig)
        self.assertIn(["https://hub.thuis.:8443", "https://hub.thuis.:8443"], cases["geldig"])


@unittest.skipUnless(shutil.which("busybox"), "geen busybox (de image gebruikt busybox sh + awk)")
class WebConfigScriptBusybox(WebConfigScript):
    """The same cases with busybox sh and busybox awk, like the nginx Alpine image."""

    shell = ("busybox", "sh")

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        awk = Path(cls._tmp.name, "awk")
        awk.write_text(f'#!/bin/sh\nexec {shutil.which("busybox")} awk "$@"\n')
        awk.chmod(0o755)
        cls.awk_dir = cls._tmp.name

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
