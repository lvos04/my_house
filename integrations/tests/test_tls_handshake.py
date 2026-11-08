"""Item 44: the pinned certificate is checked during the TLS handshake, before any request byte."""

import asyncio
import socket
import ssl

import httpx
import pytest
from test_tls import make_cert

from shc_integrations.base import AdapterError
from shc_integrations.tls import (
    CertificateMismatch,
    PinnedTransport,
    check_peer_certificate,
    client_for,
    fetch_fingerprint,
    keepalive_socket_options,
)

SECRET = b"VERY-SECRET-APPLICATION-KEY"


class RecordingServer:
    """Real local HTTPS server that records every byte received after the handshake."""

    def __init__(self, tmp_path):
        self.cp, self.kp, self.fp = make_cert(tmp_path)
        self.received = bytearray()
        self.connections = 0
        self.server = None
        self.port = 0

    async def handle(self, reader, writer):
        self.connections += 1
        try:
            while True:
                data = await asyncio.wait_for(reader.read(65536), 2.0)
                if not data:
                    break
                self.received += data
                if b"\r\n\r\n" in data:
                    body = b'{"ok":true}'
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + str(len(body)).encode()
                        + b"\r\n\r\n"
                        + body
                    )
                    await writer.drain()
        except (TimeoutError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    async def __aenter__(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cp, self.kp)
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, ssl=ctx)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()


async def test_secret_never_reaches_server_with_other_certificate(tmp_path):
    async with RecordingServer(tmp_path) as srv:
        async with client_for(f"https://127.0.0.1:{srv.port}", "00" * 32) as c:
            with pytest.raises(CertificateMismatch):
                await c.get("/clip/v2/resource", headers={"hue-application-key": SECRET.decode()})
        await asyncio.sleep(0.2)  # give the server time to read anything that might have been sent
        assert srv.connections == 1  # the handshake did happen ...
        assert SECRET not in bytes(srv.received)
        assert bytes(srv.received) == b""  # ... but not a single application byte was sent


async def test_matching_pin_reuses_verified_connection(tmp_path):
    async with RecordingServer(tmp_path) as srv:
        async with client_for(f"https://127.0.0.1:{srv.port}", srv.fp.upper()) as c:
            r1 = await c.get("/a", headers={"hue-application-key": SECRET.decode()})
            r2 = await c.get("/b")
        assert r1.json() == {"ok": True} and r2.status_code == 200
        assert srv.connections == 1  # keep-alive: the pin is verified once per connection
        assert SECRET in bytes(srv.received)


async def test_pin_with_colons_and_keepalive_socket_options(tmp_path):
    async with RecordingServer(tmp_path) as srv:
        colon_fp = ":".join(srv.fp[i : i + 2] for i in range(0, len(srv.fp), 2))
        async with client_for(
            f"https://127.0.0.1:{srv.port}", colon_fp, socket_options=keepalive_socket_options()
        ) as c:
            r = await c.get("/")
            sock = r.extensions["network_stream"].get_extra_info("socket")
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
            if hasattr(socket, "TCP_KEEPIDLE"):
                assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == 30


def test_missing_peer_certificate_is_an_error():
    class NoCert:
        def getpeercert(self, binary_form=False):
            return None

    with pytest.raises(CertificateMismatch):
        check_peer_certificate(None, "ab" * 32)
    with pytest.raises(CertificateMismatch):
        check_peer_certificate(NoCert(), "ab" * 32)


async def test_fetch_fingerprint_accepts_host_with_port(tmp_path):
    async with RecordingServer(tmp_path) as srv:
        assert await fetch_fingerprint(f"127.0.0.1:{srv.port}") == srv.fp
        assert srv.received == b""


async def test_plain_http_client_ignores_environment_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    async with client_for("http://192.168.1.10", None) as c:
        assert c._mounts == {}  # no proxy transport mounted for LAN devices


def test_pinned_transport_rejects_without_fingerprint():
    with pytest.raises(AdapterError):
        client_for("https://10.0.0.1", "")
    assert isinstance(PinnedTransport("AB:CD"), httpx.AsyncHTTPTransport)
