"""Minimal real HTTP(S) server for contract tests: a fake device on 127.0.0.1 (not a real device).

Routes map (METHOD, path) to a handler `(body: dict | None, headers: dict) -> (status, json)`.
Every request is recorded. One request per connection (`Connection: close`).
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
from collections.abc import Callable
from typing import Any

Handler = Callable[[Any, dict[str, str]], tuple[int, Any]]


class FakeHttpDevice:
    def __init__(self, routes: dict[tuple[str, str], Handler], ssl_context: ssl.SSLContext | None = None):
        self.routes = routes
        self.ssl_context = ssl_context
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
            raw = await reader.readexactly(int(headers.get("content-length", "0") or 0))
            body = json.loads(raw) if raw else None
            path = target.split("?", 1)[0]
            self.requests.append((method, path, headers, body))
            handler = self.routes.get((method, path))
            status, payload = handler(body, headers) if handler else (404, {"error": "not found"})
            data = json.dumps(payload).encode()
            writer.write(
                f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                "Connection: close\r\n\r\n".encode()
                + data
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError, ssl.SSLError):
            pass
        finally:
            writer.close()

    async def __aenter__(self) -> FakeHttpDevice:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self.ssl_context)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        assert self.server is not None
        self.server.close()

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"


def closed_tcp_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
