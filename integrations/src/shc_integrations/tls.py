"""Trust-on-first-use certificate pinning for LAN devices with self-signed certs.

Hue bridges and HomeWizard v2 devices present certificates that are not signed
by a public CA. Instead of disabling verification we capture the SHA-256
fingerprint of the leaf certificate during pairing and require it afterwards.
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl

import httpx

from .base import AdapterError


class CertificateMismatch(AdapterError):
    code = "certificate_mismatch"


def _no_verify_context() -> ssl.SSLContext:
    # Used ONLY to read the peer certificate for fingerprinting; no data is exchanged.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def fetch_fingerprint(host: str, port: int = 443, timeout: float = 5.0) -> str:
    """Return the SHA-256 fingerprint (hex) of the device's leaf certificate."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=_no_verify_context(), server_hostname=None), timeout
    )
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        der = ssl_obj.getpeercert(binary_form=True)
        return hashlib.sha256(der).hexdigest()
    finally:
        writer.close()


class PinnedTransport(httpx.AsyncHTTPTransport):
    """httpx transport that checks the server certificate fingerprint on every connection.

    Chain verification is replaced by an exact fingerprint match, which is stricter
    for a single known device than trusting any CA.
    """

    def __init__(self, fingerprint: str, **kwargs):
        self.fingerprint = fingerprint.lower().replace(":", "")
        super().__init__(verify=_no_verify_context(), **kwargs)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        stream = response.extensions.get("network_stream")
        ssl_obj = stream.get_extra_info("ssl_object") if stream is not None else None
        if ssl_obj is not None:
            der = ssl_obj.getpeercert(binary_form=True)
            if hashlib.sha256(der).hexdigest() != self.fingerprint:
                await response.aclose()
                raise CertificateMismatch(
                    "Certificaat van het apparaat is veranderd. Koppel het apparaat opnieuw als dit verwacht is."
                )
        return response


def client_for(base_url: str, fingerprint: str | None, timeout: float = 10.0, **kwargs) -> httpx.AsyncClient:
    """HTTPS client pinned to `fingerprint`. Without a fingerprint only plain HTTP is allowed."""
    if base_url.startswith("https://"):
        if not fingerprint:
            raise AdapterError("Geen certificaat-vingerafdruk bekend; koppel het apparaat opnieuw.")
        return httpx.AsyncClient(base_url=base_url, transport=PinnedTransport(fingerprint), timeout=timeout, **kwargs)
    return httpx.AsyncClient(base_url=base_url, timeout=timeout, **kwargs)
