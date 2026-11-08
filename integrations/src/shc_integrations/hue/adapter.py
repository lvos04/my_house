"""Hue Bridge v2 adapter.

Local only: pairing via the physical link button, then all traffic goes to
https://<bridge>/clip/v2 with the `hue-application-key` header. The bridge
certificate is pinned (TOFU) at pairing time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from ..base import (
    Adapter,
    AdapterError,
    ButtonEvent,
    Cap,
    Command,
    CommandResult,
    DeviceOffline,
    DiscoveredDevice,
    Event,
    Health,
    HealthState,
    PairingInstructions,
    PairingRequired,
    PairResult,
    StateUpdate,
    Unsupported,
)
from ..resilience import CircuitBreaker, RateLimiter, backoff_delays
from ..tls import client_for, fetch_fingerprint

log = logging.getLogger(__name__)

DEVICETYPE = "smart_home_control#server"
RESOURCE_TYPES = ("light", "grouped_light", "room", "zone", "scene", "device", "button", "relative_rotary")

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


def _light_state(res: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {"type": res.get("type")}
    if "on" in res:
        state["on"] = bool(res["on"].get("on"))
    if "dimming" in res and res["dimming"] is not None:
        state["brightness"] = res["dimming"].get("brightness")
    if "owner" in res:
        state["owner"] = res["owner"].get("rid")
    if "metadata" in res and "name" in res["metadata"]:
        state["name"] = res["metadata"]["name"]
    return state


def normalize_event(resource: dict[str, Any], at: datetime) -> Event | None:
    """Map one CLIP v2 resource (from GET or eventstream) to a normalized event."""
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
                event_id=f"{rid}:{report.get('updated')}",
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
            event_id=f"{rid}:{report.get('updated')}" if report.get("updated") else None,
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
    events: list[Event] = []
    for c in containers:
        at = _parse_ts(c.get("creationtime"))
        for res in c.get("data", []):
            ev = normalize_event(res, at)
            if ev is not None:
                events.append(ev)
    return events


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
        self._factory = http_factory or (lambda base, fp: client_for(base, fp))
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None
        self._resources: dict[str, dict[str, Any]] = {}
        self._breaker = CircuitBreaker(threshold=5, reset_after=30)
        self._light_limiter = RateLimiter(0.1, sleep=sleep)
        self._group_limiter = RateLimiter(1.0, sleep=sleep)

    # -- helpers ---------------------------------------------------------
    @property
    def host(self) -> str:
        host = self.config.get("host")
        if not host:
            raise AdapterError("Geen IP-adres voor de Hue Bridge ingesteld")
        return host

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._factory(f"https://{self.host}", self.credentials.get("cert_fingerprint"))
        return self._client

    def _headers(self) -> dict[str, str]:
        key = self.credentials.get("application_key")
        if not key:
            raise PairingRequired("Hue Bridge is nog niet gekoppeld")
        return {"hue-application-key": key}

    async def _get(self, path: str) -> dict[str, Any]:
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

    async def _put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        async def go():
            try:
                r = await self._http().put(path, headers=self._headers(), json=body)
            except httpx.TransportError as exc:
                raise DeviceOffline(f"Hue Bridge niet bereikbaar: {exc}") from exc
            if r.status_code == 429:
                raise AdapterError("Hue Bridge: te veel verzoeken")
            r.raise_for_status()
            return r.json()

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
        async with self._factory(f"https://{host}", fp) as c:
            r = await c.get("/api/0/config")
            r.raise_for_status()
            cfg = r.json()
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
        body = await self._get("/clip/v2/resource")
        self._resources = {r["id"]: r for r in body.get("data", []) if r.get("type") in RESOURCE_TYPES}
        return self._resources

    def resources(self, rtype: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self._resources.values() if rtype is None or r.get("type") == rtype]

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
        return set()

    async def poll(self) -> list[Event]:
        await self.load_resources()
        now = datetime.now(UTC)
        out: list[Event] = []
        for res in self._resources.values():
            if res.get("type") in ("light", "grouped_light"):
                ev = normalize_event(res, now)
                if ev is not None:
                    out.append(ev)
        return out

    async def subscribe(self) -> AsyncIterator[Event]:
        """Follow /eventstream/clip/v2 forever, reconnecting with backoff."""
        delays = backoff_delays(1.0, 60.0)
        last_id: str | None = None
        while True:
            try:
                headers = {**self._headers(), "Accept": "text/event-stream"}
                if last_id:
                    headers["Last-Event-ID"] = last_id
                async with self._http().stream(
                    "GET", "/eventstream/clip/v2", headers=headers, timeout=httpx.Timeout(10, read=None)
                ) as resp:
                    resp.raise_for_status()
                    delays = backoff_delays(1.0, 60.0)
                    buf = ""
                    async for chunk in resp.aiter_text():
                        buf += chunk.replace("\r\n", "\n")
                        while "\n\n" in buf:
                            block, buf = buf.split("\n\n", 1)
                            for msg_id, containers in parse_sse(block + "\n\n"):
                                last_id = msg_id or last_id
                                for ev in events_from_containers(containers):
                                    yield ev
            except PairingRequired:
                raise
            except (httpx.HTTPError, AdapterError) as exc:
                log.info("Hue eventstream verbroken (%s); opnieuw verbinden", exc)
            await self._sleep(next(delays))

    async def execute(self, command: Command) -> CommandResult:
        p = command.params
        rid = p.get("id") or command.device_identity
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
            await self._put(f"/clip/v2/resource/{kind}/{rid}", body)
            back = await self._get(f"/clip/v2/resource/{kind}/{rid}")
            data = (back.get("data") or [{}])[0]
            state = _light_state(data)
            confirmed = all(
                (k != "on" or state.get("on") == body["on"]["on"])
                and (k != "dimming" or abs((state.get("brightness") or 0) - body["dimming"]["brightness"]) < 1.0)
                for k in body
            )
            return CommandResult(ok=True, confirmed=confirmed, state=state)
        if command.action == "recall_scene":
            await self._group_limiter.wait("grouped_light")
            await self._put(f"/clip/v2/resource/scene/{rid}", {"recall": {"action": "active"}})
            # Scene recall has no direct readback of "active"; the eventstream reports the lights.
            return CommandResult(ok=True, confirmed=False, detail="Scène verzonden; status volgt via eventstream")
        raise Unsupported(f"Hue ondersteunt actie '{command.action}' niet")

    async def health(self) -> Health:
        if not self.credentials.get("application_key"):
            return Health(HealthState.NOT_CONFIGURED, "Nog niet gekoppeld")
        try:
            await self._get("/clip/v2/resource/bridge")
        except PairingRequired:
            return Health(HealthState.ERROR, "Sleutel geweigerd; opnieuw koppelen")
        except AdapterError as exc:
            return Health(HealthState.ERROR, str(exc))
        except httpx.HTTPError as exc:
            return Health(HealthState.ERROR, f"HTTP-fout: {exc}")
        return Health(HealthState.OK, "Verbonden")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
