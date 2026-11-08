"""Hue Bridge v2 adapter.

Local only: pairing via the physical link button, then all traffic goes to
https://<bridge>/clip/v2 with the `hue-application-key` header. The bridge
certificate is pinned (TOFU) at pairing time and checked in the TLS handshake.

The eventstream has a read timeout and a watchdog (the bridge sends no keepalives),
TCP keepalive, and a full resource reload after every (re)connect. Derived state
(group counts, active scene, reachability, button inventory and bridge rules) comes
from `HueModel`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..base import (
    Adapter,
    AdapterError,
    ButtonEvent,
    Cap,
    Command,
    CommandResult,
    DeviceInfo,
    DeviceOffline,
    DiscoveredDevice,
    Event,
    Health,
    HealthState,
    IdentityMismatch,
    PairingInstructions,
    PairingRequired,
    PairResult,
    StateUpdate,
    Unsupported,
)
from ..resilience import CircuitBreaker, RateLimiter, backoff_delays
from ..tls import client_for, fetch_fingerprint, keepalive_socket_options
from .model import RESOURCE_TYPES, HueModel, is_hue_id, light_basic_state

log = logging.getLogger(__name__)

DEVICETYPE = "smart_home_control#server"
EVENTSTREAM_READ_TIMEOUT_S = 100.0
__all__ = ["RESOURCE_TYPES", "HueAdapter", "events_from_containers", "normalize_event", "parse_sse"]

INSTRUCTIONS = PairingInstructions(
    steps=[
        "Zorg dat de Hue Bridge aan staat en met een netwerkkabel op je router is aangesloten.",
        "Vul het IP-adres van de bridge in als hij niet automatisch wordt gevonden "
        "(te vinden in de Hue-app onder Instellingen › Mijn Hue-systeem › Bridge).",
        "Druk op de ronde link-knop bovenop de bridge.",
        "Klik binnen 30 seconden op ‘Koppelen’ in deze app.",
    ],
    retry_after_s=5,
)


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


_light_state = light_basic_state


def normalize_event(resource: dict[str, Any], at: datetime, event_id_prefix: str | None = None) -> Event | None:
    """Map one CLIP v2 resource to a normalized event WITHOUT context (stateless).

    The adapter itself uses `HueModel.apply_container`, which adds group counts,
    reachability and scene information from its resource cache.
    """
    rtype = resource.get("type")
    rid = resource.get("id")
    if not rid:
        return None
    if rtype in ("light", "grouped_light"):
        state = _light_state(resource)
        if len(state) <= 1:
            return None
        return StateUpdate(device_identity=rid, state=state, observed_at=at)
    if rtype == "button":
        btn = resource.get("button") or {}
        report = btn.get("button_report")
        if report:
            return ButtonEvent(
                device_identity=(resource.get("owner") or {}).get("rid", rid),
                button=rid,
                event=report.get("event", "unknown"),
                observed_at=_parse_ts(report.get("updated")),
                event_id=f"{event_id_prefix}:{rid}" if event_id_prefix else f"{rid}:{report.get('updated')}",
            )
        if btn.get("last_event"):
            return ButtonEvent(
                device_identity=(resource.get("owner") or {}).get("rid", rid),
                button=rid,
                event=btn["last_event"],
                observed_at=at,
            )
        return None
    if rtype == "relative_rotary":
        rr = resource.get("relative_rotary") or {}
        report = rr.get("rotary_report") or rr.get("last_event")
        if not report:
            return None
        rot = report.get("rotation") or {}
        return ButtonEvent(
            device_identity=(resource.get("owner") or {}).get("rid", rid),
            button=rid,
            event=f"rotate_{rot.get('direction', 'unknown')}",
            observed_at=_parse_ts(report.get("updated")),
            event_id=(
                f"{event_id_prefix}:{rid}"
                if event_id_prefix
                else (f"{rid}:{report.get('updated')}" if report.get("updated") else None)
            ),
        )
    if rtype == "zigbee_connectivity":
        owner = (resource.get("owner") or {}).get("rid", rid)
        status = resource.get("status")
        return StateUpdate(
            device_identity=owner, state={"connectivity": status}, observed_at=at, online=status == "connected"
        )
    return None


def parse_sse(text: str) -> list[tuple[str | None, list[dict[str, Any]]]]:
    """Parse complete SSE messages. Returns (id, list of event containers)."""
    out: list[tuple[str | None, list[dict[str, Any]]]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        msg_id: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "id":
                msg_id = value
            elif field == "data":
                data_lines.append(value)
        if not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            log.warning("Hue eventstream: ongeldige JSON genegeerd")
            continue
        if isinstance(payload, dict):
            payload = [payload]
        out.append((msg_id, payload))
    return out


def events_from_containers(containers: list[dict[str, Any]]) -> list[Event]:
    """Stateless mapping (tests/tools). ButtonEvent.event_id = "<event id>:<resource id>"."""
    events: list[Event] = []
    for c in containers:
        at = _parse_ts(c.get("creationtime"))
        for res in c.get("data", []):
            ev = normalize_event(res, at, c.get("id"))
            if ev is not None:
                events.append(ev)
    return events


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class _StreamStale(Exception):
    """Watchdog: bytes arrive, but no complete eventstream message for too long."""


class HueAdapter(Adapter):
    provider = "hue"

    def __init__(
        self,
        config: dict[str, Any],
        credentials: dict[str, str] | None = None,
        *,
        http_factory: Callable[[str, str | None], httpx.AsyncClient] | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        fingerprint_fetcher: Callable[[str], Any] = fetch_fingerprint,
    ):
        self._fetch_fp = fingerprint_fetcher
        self.config = dict(config)
        self.credentials = dict(credentials or {})
        # TCP keepalive on every bridge connection: a powered-off bridge is noticed within ~1 min.
        self._factory = http_factory or (
            lambda base, fp: client_for(base, fp, socket_options=keepalive_socket_options())
        )
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None
        self._stream_client: httpx.AsyncClient | None = None
        self.model = HueModel()
        self._resources: dict[str, dict[str, Any]] = self.model.resources  # same dict; read by the worker
        self._breaker = CircuitBreaker(threshold=5, reset_after=30)
        self._light_limiter = RateLimiter(0.1, sleep=sleep)
        self._group_limiter = RateLimiter(1.0, sleep=sleep)
        # Eventstream bookkeeping (exposed in health()).
        self.subscribing = False
        self.stream_connected = False
        self.connected_since: datetime | None = None
        self.disconnected_since: datetime | None = None
        self.last_event_at: datetime | None = None
        self.last_reload_at: datetime | None = None
        self.stream_error: str | None = None
        self.connections = 0
        self.v1_rules_available: bool | None = None

    # -- helpers ---------------------------------------------------------
    @property
    def host(self) -> str:
        host = self.config.get("host")
        if not host:
            raise AdapterError("Geen IP-adres voor de Hue Bridge ingesteld")
        return host

    @property
    def read_timeout(self) -> float:
        return float(self.config.get("eventstream_read_timeout_s", EVENTSTREAM_READ_TIMEOUT_S))

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._factory(f"https://{self.host}", self.credentials.get("cert_fingerprint"))
        return self._client

    def _stream_http(self) -> httpx.AsyncClient:
        # Separate client: dropping a dead eventstream socket must not touch command connections.
        if self._stream_client is None:
            self._stream_client = self._factory(f"https://{self.host}", self.credentials.get("cert_fingerprint"))
        return self._stream_client

    async def _close_stream_client(self) -> None:
        client, self._stream_client = self._stream_client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - closing a broken connection may fail; nothing to do
                log.debug("Hue eventstream-client sluiten mislukt", exc_info=True)

    def _headers(self) -> dict[str, str]:
        key = self.credentials.get("application_key")
        if not key:
            raise PairingRequired("Hue Bridge is nog niet gekoppeld")
        return {"hue-application-key": key}

    async def _get(self, path: str) -> Any:
        async def go():
            try:
                r = await self._http().get(path, headers=self._headers())
            except httpx.TransportError as exc:
                raise DeviceOffline(f"Hue Bridge niet bereikbaar: {exc}") from exc
            if r.status_code in (401, 403):
                raise PairingRequired("Hue Bridge weigert de sleutel; koppel opnieuw")
            r.raise_for_status()
            return r.json()

        return await self._breaker.call(go)

    async def _put(self, path: str, body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """PUT; returns (body, error texts). A non-empty error list means: not confirmed."""

        async def go():
            try:
                r = await self._http().put(path, headers=self._headers(), json=body)
            except httpx.TransportError as exc:
                raise DeviceOffline(f"Hue Bridge niet bereikbaar: {exc}") from exc
            if r.status_code in (401, 403):
                raise PairingRequired("Hue Bridge weigert de sleutel; koppel opnieuw")
            if r.status_code == 429:
                raise AdapterError("Hue Bridge: te veel verzoeken")
            try:
                data = r.json()
            except ValueError:
                data = {}
            data = data if isinstance(data, dict) else {}
            errors = [
                str(e.get("description") or e) if isinstance(e, dict) else str(e) for e in data.get("errors") or []
            ]
            if r.status_code >= 400:
                raise AdapterError(
                    "Hue Bridge weigerde het commando: " + ("; ".join(errors) or f"HTTP {r.status_code}")
                )
            return data, errors

        return await self._breaker.call(go)

    # -- contract ---------------------------------------------------------
    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredDevice]:
        """mDNS `_hue._tcp`. Returns [] when zeroconf is not installed (use manual IP)."""
        try:
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
        except ImportError:
            return []
        found: dict[str, DiscoveredDevice] = {}
        azc = AsyncZeroconf()
        names: list[str] = []

        def on_change(zeroconf, service_type, name, state_change):
            if state_change is ServiceStateChange.Added:
                names.append(name)

        browser = AsyncServiceBrowser(azc.zeroconf, "_hue._tcp.local.", handlers=[on_change])
        try:
            await asyncio.sleep(timeout_s)
            for name in names:
                info = AsyncServiceInfo("_hue._tcp.local.", name)
                if not await info.async_request(azc.zeroconf, 2000):
                    continue
                addrs = info.parsed_addresses()
                props = {k.decode(): (v or b"").decode() for k, v in (info.properties or {}).items()}
                bid = props.get("bridgeid", name).lower()
                found[bid] = DiscoveredDevice(
                    provider="hue",
                    stable_identity=bid,
                    kind="hue_bridge",
                    name=name.split(".")[0],
                    host=addrs[0] if addrs else None,
                    model=props.get("modelid"),
                )
        finally:
            await browser.async_cancel()
            await azc.async_close()
        return list(found.values())

    async def probe(self, host: str) -> DiscoveredDevice:
        try:
            fp = await self._fetch_fp(host)
        except (OSError, TimeoutError) as exc:
            raise DeviceOffline(f"Geen Hue Bridge bereikbaar op {host}") from exc
        try:
            async with self._factory(f"https://{host}", fp) as c:
                r = await c.get("/api/0/config")
                r.raise_for_status()
                cfg = r.json()
        except httpx.TransportError as exc:
            raise DeviceOffline(f"Geen Hue Bridge bereikbaar op {host}") from exc
        bid = str(cfg.get("bridgeid", "")).lower()
        if not bid:
            raise AdapterError(f"{host} lijkt geen Hue Bridge te zijn")
        return DiscoveredDevice(
            provider="hue",
            stable_identity=bid,
            kind="hue_bridge",
            name=cfg.get("name", "Hue Bridge"),
            host=host,
            model=cfg.get("modelid"),
            firmware=cfg.get("swversion"),
            extra={"apiversion": cfg.get("apiversion"), "cert_fingerprint": fp},
        )

    async def pair(self, host: str, **kwargs: Any) -> PairResult | PairingInstructions:
        dev = await self.probe(host)
        expected = kwargs.get("expected_bridge_id")
        if expected and str(expected).lower() != dev.stable_identity:
            raise IdentityMismatch(
                f"Op {host} antwoordt een andere Hue Bridge ({dev.stable_identity}, verwacht {expected}).",
                diagnostics={"host": host, "expected_bridge_id": expected, "found_bridge_id": dev.stable_identity},
            )
        fp = dev.extra["cert_fingerprint"]
        async with self._factory(f"https://{host}", fp) as c:
            r = await c.post("/api", json={"devicetype": DEVICETYPE, "generateclientkey": True})
            r.raise_for_status()
            body = r.json()
        item = body[0] if isinstance(body, list) and body else {}
        if "error" in item:
            if item["error"].get("type") == 101:
                return INSTRUCTIONS
            raise AdapterError(f"Hue koppelen mislukt: {item['error'].get('description')}")
        success = item.get("success") or {}
        if "username" not in success:
            raise AdapterError("Onverwacht antwoord van de Hue Bridge")
        creds = {
            "application_key": success["username"],
            "client_key": success.get("clientkey", ""),
            "cert_fingerprint": fp,
            "bridge_id": dev.stable_identity,
        }
        self.config["host"] = host
        self.credentials = creds
        self._client = None
        return PairResult(stable_identity=dev.stable_identity, credentials=creds, device=dev)

    def instructions(self) -> PairingInstructions:
        return INSTRUCTIONS

    async def load_resources(self) -> dict[str, dict[str, Any]]:
        """Full reload: all v2 resources, plus the v1 rules (best effort) for the bridge rules."""
        body = await self._get("/clip/v2/resource")
        self.model.load(body.get("data", []))
        try:
            rules = await self._get(f"/api/{self.credentials.get('application_key')}/rules")
            if isinstance(rules, dict) and not (isinstance(rules.get("error"), dict)):
                self.model.set_v1_rules(rules)
                self.v1_rules_available = True
            else:
                self.v1_rules_available = False
        except (AdapterError, httpx.HTTPError, ValueError) as exc:
            log.debug("Hue v1-regels niet leesbaar: %s", exc)
            self.v1_rules_available = False
        self.last_reload_at = datetime.now(UTC)
        return self._resources

    async def full_reload_events(self) -> list[Event]:
        await self.load_resources()
        return self.model.snapshot(datetime.now(UTC))

    def resources(self, rtype: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self._resources.values() if rtype is None or r.get("type") == rtype]

    def device_info(self, device_identity: str) -> DeviceInfo | None:
        return self.model.info_for(device_identity, datetime.now(UTC))

    def inventory(self) -> list[DeviceInfo]:
        return self.model.inventory(datetime.now(UTC))

    async def get_capabilities(self, device_identity: str) -> set[Cap]:
        if not self._resources:
            await self.load_resources()
        res = self._resources.get(device_identity)
        if res is None:
            return set()
        t = res.get("type")
        if t in ("light", "grouped_light"):
            caps = {Cap.POWER}
            if res.get("dimming") is not None:
                caps.add(Cap.BRIGHTNESS)
            return caps
        if t in ("room", "zone"):
            return {Cap.SCENE}
        if t in ("button", "relative_rotary"):
            return {Cap.BUTTON_EVENT}
        if t == "device" and device_identity in self.model.button_devices():
            return {Cap.BUTTON_EVENT}
        return set()

    async def poll(self) -> list[Event]:
        """Full reload: inventory (DeviceInfo) and current state of lights, groups and buttons."""
        return await self.full_reload_events()

    async def _sse_messages(self, resp: httpx.Response) -> AsyncIterator[tuple[str | None, list[dict[str, Any]]]]:
        """Complete SSE messages; raises TimeoutError/_StreamStale when the stream goes quiet."""
        chunks = resp.aiter_text().__aiter__()
        buf = ""
        last_message = time.monotonic()
        while True:
            try:
                async with asyncio.timeout(self.read_timeout):
                    chunk = await anext(chunks)
            except StopAsyncIteration:
                return
            buf += chunk.replace("\r\n", "\n")
            complete = False
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                complete = True  # also ": hi" comments prove the stream is alive
                for msg in parse_sse(block + "\n\n"):
                    yield msg
            now = time.monotonic()
            if complete:
                last_message = now
            elif now - last_message > self.read_timeout:
                raise _StreamStale("Hue eventstream levert geen volledige berichten meer")

    async def subscribe(self) -> AsyncIterator[Event]:
        """Follow /eventstream/clip/v2 forever.

        Read timeout + watchdog (the bridge sends no keepalives, so a silent or dead stream is
        reconnected after `eventstream_read_timeout_s`), TCP keepalive on the socket, and a full
        resource reload after every (re)connect so nothing missed while disconnected stays stale.
        """
        self.subscribing = True
        delays = backoff_delays(1.0, 60.0)
        last_id: str | None = None
        while True:
            quiet = False
            try:
                headers = {**self._headers(), "Accept": "text/event-stream"}
                if last_id:
                    headers["Last-Event-ID"] = last_id
                timeout = httpx.Timeout(10.0, read=self.read_timeout)
                async with self._stream_http().stream(
                    "GET", "/eventstream/clip/v2", headers=headers, timeout=timeout
                ) as resp:
                    if resp.status_code in (401, 403):
                        raise PairingRequired("Hue Bridge weigert de sleutel; koppel opnieuw")
                    resp.raise_for_status()
                    self.stream_connected, self.stream_error = True, None
                    self.connected_since, self.disconnected_since = datetime.now(UTC), None
                    self.connections += 1
                    delays = backoff_delays(1.0, 60.0)
                    try:
                        for ev in await self.full_reload_events():
                            yield ev
                    except (AdapterError, httpx.HTTPError) as exc:
                        log.info("Hue: volledig herladen na verbinden mislukt (%s)", exc)
                    async for msg_id, containers in self._sse_messages(resp):
                        last_id = msg_id or last_id
                        self.last_event_at = datetime.now(UTC)
                        for c in containers:
                            for ev in self.model.apply_container(c, msg_id):
                                yield ev
                    quiet = True  # bridge closed the stream normally
            except PairingRequired:
                raise
            except (TimeoutError, httpx.TimeoutException, _StreamStale) as exc:
                quiet = True  # no data within the read timeout: reconnect (no error, no backoff)
                log.debug("Hue eventstream stil (%s); opnieuw verbinden", exc)
            except (httpx.HTTPError, AdapterError) as exc:
                self.stream_error = str(exc) or type(exc).__name__
                log.info("Hue eventstream verbroken (%s); opnieuw verbinden", self.stream_error)
            finally:
                if self.stream_connected or self.disconnected_since is None:
                    self.disconnected_since = datetime.now(UTC)
                self.stream_connected = False
                await self._close_stream_client()
            await self._sleep(1.0 if quiet else next(delays))

    async def execute(self, command: Command) -> CommandResult:
        p = command.params
        rid = p.get("id") or command.device_identity
        if command.action not in ("set_light", "set_group", "recall_scene"):
            raise Unsupported(f"Hue ondersteunt actie '{command.action}' niet")
        if not is_hue_id(rid):
            raise AdapterError(f"Ongeldige Hue-id '{rid}'")
        if command.action in ("set_light", "set_group"):
            kind = "light" if command.action == "set_light" else "grouped_light"
            body: dict[str, Any] = {}
            if "on" in p:
                body["on"] = {"on": bool(p["on"])}
            if p.get("brightness") is not None:
                b = float(p["brightness"])
                if not 0 <= b <= 100:
                    raise AdapterError("Helderheid moet tussen 0 en 100 liggen")
                body["dimming"] = {"brightness": b}
            if not body:
                raise AdapterError("Leeg lichtcommando")
            limiter = self._light_limiter if kind == "light" else self._group_limiter
            await limiter.wait(kind)
            _, errors = await self._put(f"/clip/v2/resource/{kind}/{rid}", body)
            back = await self._get(f"/clip/v2/resource/{kind}/{rid}")
            data = (back.get("data") or [{}])[0]
            state = _light_state(data)
            if errors:
                # The bridge accepted the request but says it may not have effect: never "confirmed".
                return CommandResult(
                    ok=True,
                    confirmed=False,
                    detail="Hue Bridge meldt: " + "; ".join(errors),
                    state=state,
                    rejected=True,
                )
            confirmed = all(
                (k != "on" or state.get("on") == body["on"]["on"])
                and (k != "dimming" or abs((state.get("brightness") or 0) - body["dimming"]["brightness"]) < 1.0)
                for k in body
            )
            detail = "" if confirmed else "Hue Bridge bevestigt de gevraagde stand niet (readback wijkt af)"
            return CommandResult(ok=True, confirmed=confirmed, detail=detail, state=state)
        await self._group_limiter.wait("grouped_light")
        _, errors = await self._put(f"/clip/v2/resource/scene/{rid}", {"recall": {"action": "active"}})
        if errors:
            return CommandResult(
                ok=True, confirmed=False, detail="Hue Bridge meldt: " + "; ".join(errors), rejected=True
            )
        back = await self._get(f"/clip/v2/resource/scene/{rid}")
        active = ((back.get("data") or [{}])[0].get("status") or {}).get("active")
        if active is None:
            return CommandResult(
                ok=True, confirmed=False, detail="Scène verzonden; deze bridge meldt niet welke scène actief is"
            )
        confirmed = active != "inactive"
        return CommandResult(
            ok=True,
            confirmed=confirmed,
            detail="" if confirmed else "Bridge meldt de scène niet als actief",
            state={"scene_id": rid, "active": active},
        )

    def _health_extra(self) -> dict[str, Any]:
        return {
            "bridge_id": self.credentials.get("bridge_id"),
            "eventstream_connected": self.stream_connected,
            "connected_since": _iso(self.connected_since),
            "disconnected_since": _iso(self.disconnected_since) if self.subscribing else None,
            "last_event_at": _iso(self.last_event_at),
            "last_reload_at": _iso(self.last_reload_at),
            "eventstream_error": self.stream_error,
            "connections": self.connections,
            "v1_rules_available": self.v1_rules_available,
            "breaker": self._breaker.state,
        }

    async def health(self) -> Health:
        if not self.credentials.get("application_key"):
            return Health(HealthState.NOT_CONFIGURED, "Nog niet gekoppeld", extra=self._health_extra())
        try:
            body = await self._get("/clip/v2/resource/bridge")
            found = str(((body.get("data") or [{}])[0]).get("bridge_id") or "").lower()
            expected = str(self.credentials.get("bridge_id") or "").lower()
            if expected and found and found != expected:
                raise IdentityMismatch(
                    f"Op {self.host} antwoordt een andere Hue Bridge ({found}, verwacht {expected}).",
                    diagnostics={"host": self.host, "expected_bridge_id": expected, "found_bridge_id": found},
                )
        except PairingRequired as exc:
            return Health(
                HealthState.ERROR, "Sleutel geweigerd; opnieuw koppelen", code=exc.code, extra=self._health_extra()
            )
        except AdapterError as exc:
            return Health(HealthState.ERROR, str(exc), code=exc.code, extra=self._health_extra())
        except httpx.HTTPError as exc:
            return Health(HealthState.ERROR, f"HTTP-fout: {exc}", code="http_error", extra=self._health_extra())
        grace = timedelta(seconds=float(self.config.get("eventstream_grace_s", 120)))
        down_since = self.disconnected_since
        if self.subscribing and not self.stream_connected and down_since and datetime.now(UTC) - down_since > grace:
            return Health(
                HealthState.WARN,
                "Verbonden, maar knopgebeurtenissen komen niet binnen (eventstream verbroken sinds "
                f"{down_since.astimezone(UTC):%H:%M} UTC)",
                code="eventstream_disconnected",
                extra=self._health_extra(),
            )
        return Health(HealthState.OK, "Verbonden", extra=self._health_extra())

    def diagnostics(self) -> dict[str, Any]:
        return {
            "host": self.config.get("host"),
            **self._health_extra(),
            "resources": {t: len(self.model.of_type(t)) for t in RESOURCE_TYPES},
        }

    async def close(self) -> None:
        await self._close_stream_client()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
