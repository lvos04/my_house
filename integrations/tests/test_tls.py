import asyncio
import datetime as dt
import hashlib
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from shc_integrations.base import AdapterError
from shc_integrations.tls import CertificateMismatch, client_for, fetch_fingerprint


def make_cert(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-bridge")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cp, kp = tmp_path / "c.pem", tmp_path / "k.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    fp = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return cp, kp, fp


async def handle(reader, writer):
    await reader.readuntil(b"\r\n\r\n")
    body = b'{"ok":true}'
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\nConnection: close\r\n\r\n"
        + body
    )
    await writer.drain()
    writer.close()


@pytest.fixture
async def server(tmp_path):
    cp, kp, fp = make_cert(tmp_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cp, kp)
    srv = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
    port = srv.sockets[0].getsockname()[1]
    yield port, fp
    srv.close()


async def test_fingerprint_and_pinned_request(server):
    port, fp = server
    assert await fetch_fingerprint("127.0.0.1", port) == fp
    async with client_for(f"https://127.0.0.1:{port}", fp) as c:
        r = await c.get("/api/0/config")
        assert r.json() == {"ok": True}


async def test_pin_mismatch_rejected(server):
    port, _ = server
    async with client_for(f"https://127.0.0.1:{port}", "00" * 32) as c:
        with pytest.raises(CertificateMismatch):
            await c.get("/")


def test_https_without_pin_refused():
    with pytest.raises(AdapterError):
        client_for("https://10.0.0.1", None)
