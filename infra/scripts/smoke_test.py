#!/usr/bin/env python3
"""Smoke test for a running stack, through nginx (python standard library only).

    smoke_test.py --base http://127.0.0.1:8088 [--edge-network smart-home-control_edge ...]
    smoke_test.py --base http://127.0.0.1:8088 --expect-unavailable   (with the api container stopped)

Checks: health, cache headers, first-time setup, home creation, the live connection (WebSocket)
on a non-default port, a backup made through the API, the body-size limit, the login rate limit
and, optionally, the tunnel path (X-Forwarded-Proto/CF-Connecting-IP only trusted from cloudflared).
Prints the backup file name on the last line (or writes it to --backup-file-out).
"""

from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

USERNAME = "rooktest-beheer"


class Fail(Exception):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise Fail(msg)
    print(f"ok    {msg}")


class Client:
    def __init__(self, base: str) -> None:
        if urlsplit(base).scheme not in ("http", "https"):
            raise Fail(f"--base moet met http:// of https:// beginnen: {base}")
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf: str | None = None

    def request(self, method: str, path: str, body=None, headers: dict | None = None, raw: bytes | None = None):
        h = {"Accept": "application/json", **(headers or {})}
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if self.csrf and method not in ("GET", "HEAD"):
            h.setdefault("X-CSRF-Token", self.csrf)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)  # noqa: S310 - http(s) only
        try:
            resp = self.opener.open(req, timeout=60)
        except urllib.error.HTTPError as e:
            resp = e
        payload = resp.read()
        try:
            parsed = json.loads(payload) if payload else None
        except ValueError:
            parsed = payload.decode(errors="replace")
        return resp.status, resp.headers, parsed

    def cookie_header(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.jar)


def wait_health(c: Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            status, _h, body = c.request("GET", "/api/v1/health")
            last = (status, body)
            if status == 200:
                check(True, f"health via nginx: {body}")
                return
        except OSError as exc:
            last = exc
        time.sleep(2)
    raise Fail(f"API niet gezond binnen {timeout:.0f} s (laatste antwoord: {last})")


def ws_hello(base: str, cookie: str) -> dict:
    """Open /api/v1/ws through nginx with the session cookie and return the first message."""
    u = urlsplit(base)
    host, port = u.hostname, u.port or 80
    s = socket.create_connection((host, port), timeout=15)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /api/v1/ws HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {u.scheme}://{u.netloc}\r\n"
            f"Cookie: {cookie}\r\n\r\n"
        )
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise Fail("WebSocket: verbinding gesloten tijdens de handshake")
            buf += chunk
        head, rest = buf.split(b"\r\n\r\n", 1)
        status_line = head.split(b"\r\n", 1)[0].decode()
        check(" 101 " in status_line, f"WebSocket-handshake via nginx op poort {port} ({status_line})")

        def need(n: int) -> bytes:
            nonlocal rest
            while len(rest) < n:
                chunk = s.recv(4096)
                if not chunk:
                    raise Fail("WebSocket: verbinding gesloten")
                rest += chunk
            out, rest = rest[:n], rest[n:]
            return out

        b1, b2 = need(2)
        opcode, length = b1 & 0x0F, b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", need(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", need(8))[0]
        payload = need(length)
        if opcode == 8:
            code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
            raise Fail(f"WebSocket gesloten door de server (code {code}): {payload[2:].decode(errors='replace')}")
        return json.loads(payload)
    finally:
        s.close()


def edge_request(args, path: str, body: dict, headers: dict) -> tuple[int, dict]:
    """POST from a throw-away container that holds cloudflared's address on the edge network."""
    script = (
        "import json,sys,urllib.request,urllib.error\n"
        "a=json.loads(sys.argv[1])\n"
        "r=urllib.request.Request(a['url'],data=json.dumps(a['body']).encode(),method='POST',"
        "headers={'Content-Type':'application/json','Accept':'application/json',**a['headers']})\n"
        "try:\n  x=urllib.request.urlopen(r,timeout=30)\nexcept urllib.error.HTTPError as e:\n  x=e\n"
        "print(json.dumps({'status':x.status,'headers':[[k,v] for k,v in x.headers.items()]}))\n"
    )
    arg = json.dumps({"url": f"http://{args.web_ip}:8080{path}", "body": body, "headers": headers})
    cmd = [
        "docker", "run", "--rm", "--network", args.edge_network, "--ip", args.tunnel_ip,
        "--entrypoint", "python", args.client_image, "-c", script, arg,
    ]  # fmt: skip
    out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120).stdout  # noqa: S603
    res = json.loads(out.strip().splitlines()[-1])
    hdrs: dict[str, list[str]] = {}
    for k, v in res["headers"]:
        hdrs.setdefault(k.lower(), []).append(v)
    return res["status"], hdrs


def expect_unavailable(base: str) -> None:
    c = Client(base)
    status, headers, body = c.request("GET", "/api/v1/health")
    check(status == 503, f"API gestopt: JSON-verzoek krijgt 503 (kreeg {status})")
    check(isinstance(body, dict) and body.get("detail", {}).get("code") == "api_unavailable", f"JSON-melding: {body}")
    check(headers.get("Retry-After") == "5", "Retry-After: 5")
    status, headers, body = c.request("GET", "/api/v1/docs", headers={"Accept": "text/html,application/xhtml+xml"})
    check(status == 503 and "text/html" in (headers.get("Content-Type") or ""), "browser krijgt een HTML-pagina")
    check(
        "De app start of wordt bijgewerkt" in str(body) and 'http-equiv="refresh"' in str(body),
        "pagina met automatisch opnieuw proberen",
    )
    status, _h, _b = c.request("GET", "/")
    check(status == 200, "de app zelf blijft bereikbaar terwijl de API weg is")


def run(args) -> str:
    c = Client(args.base)
    wait_health(c, args.timeout)

    status, headers, _ = c.request("GET", "/healthz", headers={"Accept": "text/plain"})
    check(status == 200, "nginx /healthz")
    status, headers, _ = c.request("GET", "/", headers={"Accept": "text/html"})
    check(
        status == 200 and headers.get("Cache-Control") == "no-cache",
        f"index.html: Cache-Control {headers.get('Cache-Control')}",
    )
    check(headers.get("X-Content-Type-Options") == "nosniff", "beveiligingsheaders aanwezig")
    check("Strict-Transport-Security" not in headers, "geen HSTS via http")

    password = args.password or secrets.token_urlsafe(18)
    # Spoofed proxy headers from the LAN must be ignored: the cookie stays without "Secure".
    status, headers, body = c.request(
        "POST", "/api/v1/auth/setup", {"username": USERNAME, "password": password},
        headers={"X-Forwarded-Proto": "https", "CF-Connecting-IP": "198.51.100.9", "X-Forwarded-For": "198.51.100.9"},
    )  # fmt: skip
    check(status == 200, f"beheerder aangemaakt via /auth/setup (status {status}: {body if status != 200 else ''})")
    c.csrf = body["csrf_token"]
    cookies = headers.get_all("Set-Cookie") or []
    check(cookies and not any("secure" in x.lower().split("; ") for x in cookies),
          "thuis via http: cookie zonder Secure, ook met vervalste X-Forwarded-Proto")  # fmt: skip

    status, _h, home = c.request("POST", "/api/v1/homes", {"name": "Rooktest", "timezone": "Europe/Amsterdam"})
    check(status == 200 and "id" in home, f"woning aangemaakt (status {status})")

    msg = ws_hello(args.base, c.cookie_header())
    check(msg.get("type") == "hello", f"live-verbinding geeft 'hello' ({msg.get('type')})")

    passphrase = args.passphrase or secrets.token_urlsafe(24)
    status, _h, rec = c.request("POST", "/api/v1/system/backups", {"passphrase": passphrase, "include_secrets": True})
    check(
        status in (200, 202), f"back-up gestart via de API (status {status}: {rec if status not in (200, 202) else ''})"
    )
    deadline = time.monotonic() + 300
    while rec.get("status") == "running" and time.monotonic() < deadline:
        time.sleep(3)
        _s, _h, rows = c.request("GET", "/api/v1/system/backups")
        rec = next((r for r in rows if r["id"] == rec["id"]), rec)
    check(rec.get("status") == "ok", f"back-up klaar: {rec.get('filename')} ({rec.get('size_bytes')} bytes)")

    status, _h, body = c.request("POST", "/api/v1/auth/login", raw=b"x" * (2 * 1024 * 1024),
                                 headers={"Content-Type": "application/json"})  # fmt: skip
    check(
        status == 413 and body.get("detail", {}).get("code") == "request_too_large",
        f"2 MB verzoek geweigerd met 413 ({status})",
    )

    if args.edge_network:
        cred = {"username": USERNAME, "password": password}
        status, hdrs = edge_request(args, "/api/v1/auth/login", cred, {
            "X-Forwarded-Proto": "https", "CF-Connecting-IP": "203.0.113.7", "Host": "huis.example.nl"})  # fmt: skip
        sc = hdrs.get("set-cookie", [])
        check(status == 200 and sc and all("secure" in x.lower().split("; ") for x in sc),
              "via de tunnel over https: cookie met Secure")  # fmt: skip
        check(bool(hdrs.get("strict-transport-security")), "via de tunnel over https: HSTS")
        status, hdrs = edge_request(
            args, "/api/v1/auth/login", cred, {"X-Forwarded-Proto": "http", "Host": "huis.example.nl"}
        )
        sc = hdrs.get("set-cookie", [])
        check(status == 200 and sc and not any("secure" in x.lower().split("; ") for x in sc),
              "via de tunnel over http: cookie zonder Secure")  # fmt: skip

    statuses = []
    for _ in range(10):
        s, _h, b = Client(args.base).request(
            "POST", "/api/v1/auth/login", {"username": "bestaat-niet", "password": "fout-wachtwoord"}
        )
        statuses.append(s)
        if s == 429:
            check(
                isinstance(b, dict) and b.get("detail", {}).get("code") == "rate_limited",
                "429 met Nederlandse JSON-melding",
            )
            break
    check(429 in statuses, f"inloglimiet in nginx ({statuses})")
    return rec["filename"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8088")
    ap.add_argument("--timeout", type=float, default=240)
    ap.add_argument("--password", help="admin password to create (default: random)")
    ap.add_argument(
        "--passphrase",
        default=os.environ.get("SHC_SMOKE_PASSPHRASE"),
        help="backup passphrase (or SHC_SMOKE_PASSPHRASE)",
    )
    ap.add_argument("--backup-file-out", help="write the backup file name here")
    ap.add_argument("--expect-unavailable", action="store_true", help="only check the answers while the API is down")
    ap.add_argument("--edge-network", help="docker network 'edge' to test the tunnel path from")
    ap.add_argument("--tunnel-ip", default="172.30.250.2")
    ap.add_argument("--web-ip", default="172.30.250.3", help="web's address on the edge network")
    ap.add_argument("--client-image", help="image with python, used as the fake cloudflared (e.g. the API image)")
    args = ap.parse_args(argv)
    if args.edge_network and not args.client_image:
        ap.error("--edge-network heeft --client-image nodig")
    try:
        if args.expect_unavailable:
            expect_unavailable(args.base)
            return 0
        name = run(args)
    except Fail as exc:
        print(f"FOUT  {exc}", file=sys.stderr)
        return 1
    if args.backup_file_out:
        with open(args.backup_file_out, "w", encoding="utf-8") as f:
            f.write(name + "\n")
    print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
