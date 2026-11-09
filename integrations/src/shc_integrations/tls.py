"""Trust-on-first-use certificate pinning for LAN devices with self-signed certs.

Hue bridges and HomeWizard v2 devices present certificates that are not signed
by a public CA. Instead of disabling verification we capture the SHA-256
fingerprint of the leaf certificate during pairing and require it afterwards.

The fingerprint is checked DURING connection setup: right after the TLS handshake
and before httpcore writes a single byte of the HTTP request. A device that took
over the IP address of the bridge or meter therefore never receives the
application key or token. Missing certificate information counts as a mismatch.
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import ssl
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx

from .base import AdapterError


class CertificateMismatch(AdapterError):
    code = "certificate_mismatch"


MISMATCH_TEXT = "Certificaat van het apparaat is veranderd. Koppel het apparaat opnieuw als dit verwacht is."
NO_CERT_TEXT = "Het apparaat stuurde geen certificaat mee; verbinding geweigerd."


def normalize_fingerprint(fingerprint: str) -> str:
    return fingerprint.lower().replace(":", "").strip()


def fingerprint_of(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def _no_verify_context() -> ssl.SSLContext:
    # Chain/hostname verification is replaced by the exact fingerprint check below.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def check_peer_certificate(ssl_object: Any, fingerprint: str) -> None:
    """Raise CertificateMismatch unless the peer's leaf certificate has `fingerprint`."""
    der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
    if not der:
        raise CertificateMismatch(NO_CERT_TEXT)
    if fingerprint_of(der) != normalize_fingerprint(fingerprint):
        raise CertificateMismatch(MISMATCH_TEXT)


def keepalive_socket_options(idle_s: int = 30, interval_s: int = 10, count: int = 3) -> list[tuple[int, int, int]]:
    """TCP keepalive so a vanished peer (power loss, IP change) is noticed within ~1 minute."""
    opts = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    if hasattr(socket, "TCP_KEEPIDLE"):  # Linux
        opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, idle_s))
    elif hasattr(socket, "TCP_KEEPALIVE"):  # macOS
        opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, idle_s))
    if hasattr(socket, "TCP_KEEPINTVL"):
        opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_s))
    if hasattr(socket, "TCP_KEEPCNT"):
        opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, count))
    return opts


async def fetch_fingerprint(host: str, port: int = 443, timeout: float = 5.0) -> str:
    """Return the SHA-256 fingerprint (hex) of the device's leaf certificate.

    Only performs a TLS handshake; no application data is sent. `host` may be "ip:port".
    """
    if ":" in host and host.count(":") == 1:
        host, _, p = host.partition(":")
        port = int(p)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=_no_verify_context(), server_hostname=None), timeout
    )
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        der = ssl_obj.getpeercert(binary_form=True) if ssl_obj is not None else None
        if not der:
            raise AdapterError(NO_CERT_TEXT)
        return fingerprint_of(der)
    finally:
        writer.close()


class _PinnedStream(httpcore.AsyncNetworkStream):
    """Network stream whose TLS upgrade only succeeds when the peer certificate matches the pin."""

    def __init__(self, inner: httpcore.AsyncNetworkStream, fingerprint: str):
        self._inner = inner
        self._fingerprint = fingerprint

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return await self._inner.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._inner.write(buffer, timeout)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def start_tls(
        self, ssl_context: ssl.SSLContext, server_hostname: str | None = None, timeout: float | None = None
    ) -> httpcore.AsyncNetworkStream:
        tls = await self._inner.start_tls(ssl_context, server_hostname, timeout)
        try:
            check_peer_certificate(tls.get_extra_info("ssl_object"), self._fingerprint)
        except CertificateMismatch:
            await tls.aclose()  # nothing has been written on this connection yet
            raise
        return _PinnedStream(tls, self._fingerprint)

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class _PinningBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, fingerprint: str, inner: httpcore.AsyncNetworkBackend | None = None):
        self._inner = inner or httpcore.AnyIOBackend()
        self._fingerprint = fingerprint

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        stream = await self._inner.connect_tcp(
            host, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )
        return _PinnedStream(stream, self._fingerprint)

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None
    ) -> httpcore.AsyncNetworkStream:  # pragma: no cover - not used for devices
        raise AdapterError("Unix-sockets worden niet ondersteund voor apparaten")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PinnedTransport(httpx.AsyncHTTPTransport):
    """httpx transport that requires the server certificate fingerprint on every new connection.

    Chain verification is replaced by an exact fingerprint match, which is stricter for a single
    known device than trusting any CA. The check runs in the TLS handshake step of the
    connection, before the request (with its secret headers) is written.
    """

    def __init__(
        self,
        fingerprint: str,
        *,
        socket_options: Iterable[Any] | None = None,
        limits: httpx.Limits | None = None,
        retries: int = 0,
    ):
        limits = limits or httpx.Limits(max_connections=10, max_keepalive_connections=5)
        self.fingerprint = normalize_fingerprint(fingerprint)
        super().__init__(verify=_no_verify_context(), socket_options=socket_options, limits=limits, retries=retries)
        # Replace the default pool by one whose network backend enforces the pin in start_tls().
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=_no_verify_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            retries=retries,
            socket_options=socket_options,
            network_backend=_PinningBackend(self.fingerprint),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        # Defence in depth: should the pool ever bypass the backend above, still refuse the answer.
        stream = response.extensions.get("network_stream")
        try:
            check_peer_certificate(
                stream.get_extra_info("ssl_object") if stream is not None else None, self.fingerprint
            )
        except CertificateMismatch:
            await response.aclose()
            raise
        return response


def client_for(
    base_url: str,
    fingerprint: str | None,
    timeout: float = 10.0,
    *,
    socket_options: Iterable[Any] | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """HTTPS client pinned to `fingerprint`. Without a fingerprint only plain HTTP is allowed.

    Environment proxies and .netrc are ignored (`trust_env=False`): LAN devices are always
    contacted directly.
    """
    kwargs.setdefault("trust_env", False)
    if base_url.startswith("https://"):
        if not fingerprint:
            raise AdapterError("Geen certificaat-vingerafdruk bekend; koppel het apparaat opnieuw.")
        return httpx.AsyncClient(
            base_url=base_url,
            transport=PinnedTransport(fingerprint, socket_options=socket_options),
            timeout=timeout,
            **kwargs,
        )
    transport = httpx.AsyncHTTPTransport(socket_options=socket_options) if socket_options else None
    return httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport, **kwargs)
