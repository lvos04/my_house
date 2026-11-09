"""HomeWizard Energy local API (v1 over HTTP, v2 over HTTPS with token).

Products: P1 meter (HWE-P1), Energy Socket (HWE-SKT), kWh meters (HWE-KWH1,
HWE-KWH3, SDM230-wifi, SDM630-wifi). Readings are independent of the
HomeWizard cloud.

The relay of an Energy Socket is NEVER switched unless the injected
`relay_allowed` policy hook explicitly allows it for that device. The default
denies everything, so a protected boiler socket can not be switched from here.
Its relay state (`relay_on`) and `switch_lock` are read and reported every poll.

Identity: the serial number is checked on every poll and health check; when another
HomeWizard device answers on the configured address (DHCP change) the adapter raises
`IdentityMismatch` ("identity_mismatch") and emits no readings.

Time: API v2 sends local timestamps without a time zone. They are resolved to the
UTC candidate closest to the moment we received them (the autumn double hour has two
candidates, the spring gap none that exists), and replaced by the receive time when
even the best candidate is more than an hour off.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..base import (
    Adapter,
    AdapterError,
    Cap,
    Command,
    CommandResult,
    DeviceOffline,
    DiscoveredDevice,
    Event,
    Health,
    HealthState,
    IdentityMismatch,
    PairingInstructions,
    PairingRequired,
    PairResult,
    PolicyDenied,
    Quality,
    Reading,
    StateUpdate,
    Unsupported,
    network_reason,
)
from ..resilience import CircuitBreaker, RateLimiter
from ..tls import client_for, fetch_fingerprint

log = logging.getLogger(__name__)

USER_NAME = "local/smart_home_control"

PRODUCTS: dict[str, dict[str, Any]] = {
    "HWE-P1": {"kind": "p1_meter", "label": "P1-meter", "v2": True, "relay": False, "gas": True},
    "HWE-SKT": {"kind": "energy_socket", "label": "Energy Socket", "v2": False, "relay": True, "gas": False},
    "HWE-KWH1": {"kind": "kwh_meter", "label": "kWh-meter 1-fase", "v2": True, "relay": False, "gas": False},
    "HWE-KWH3": {"kind": "kwh_meter", "label": "kWh-meter 3-fase", "v2": True, "relay": False, "gas": False},
    "SDM230-wifi": {
        "kind": "kwh_meter",
        "label": "kWh-meter 1-fase (SDM230)",
        "v2": False,
        "relay": False,
        "gas": False,
    },
    "SDM630-wifi": {
        "kind": "kwh_meter",
        "label": "kWh-meter 3-fase (SDM630)",
        "v2": False,
        "relay": False,
        "gas": False,
    },
}

INSTRUCTIONS = PairingInstructions(
    steps=[
        "Open de HomeWizard Energy-app en zorg dat het apparaat met je wifi verbonden is.",
        "Ga naar Instellingen › Meters › (je apparaat) en zet ‘Lokale API’ aan.",
        "Vul het IP-adres van het apparaat in als het niet automatisch wordt gevonden.",
        "Voor de nieuwere v2-API (P1-meter, kWh-meter): druk na ‘Koppelen’ binnen 30 seconden "
        "kort op de knop van het apparaat.",
    ],
    retry_after_s=5,
)

# v1 field -> (metric, unit)
V1_FIELDS = {
    "active_power_w": ("power_w", "W"),
    "total_power_import_kwh": ("energy_import_kwh", "kWh"),
    "total_power_export_kwh": ("energy_export_kwh", "kWh"),
    "total_power_import_t1_kwh": ("energy_import_t1_kwh", "kWh"),
    "total_power_import_t2_kwh": ("energy_import_t2_kwh", "kWh"),
    "total_power_export_t1_kwh": ("energy_export_t1_kwh", "kWh"),
    "total_power_export_t2_kwh": ("energy_export_t2_kwh", "kWh"),
}
V2_FIELDS = {
    "power_w": ("power_w", "W"),
    "energy_import_kwh": ("energy_import_kwh", "kWh"),
    "energy_export_kwh": ("energy_export_kwh", "kWh"),
    "energy_import_t1_kwh": ("energy_import_t1_kwh", "kWh"),
    "energy_import_t2_kwh": ("energy_import_t2_kwh", "kWh"),
    "energy_export_t1_kwh": ("energy_export_t1_kwh", "kWh"),
    "energy_export_t2_kwh": ("energy_export_t2_kwh", "kWh"),
}


def product_info(product_type: str | None) -> dict[str, Any]:
    return PRODUCTS.get(
        product_type or "",
        {"kind": "unknown", "label": product_type or "onbekend", "v2": False, "relay": False, "gas": False},
    )


def capabilities_for(product_type: str | None) -> set[Cap]:
    info = product_info(product_type)
    if info["kind"] == "unknown":
        return set()
    caps = {Cap.MEASURED_POWER, Cap.ENERGY_IMPORT, Cap.ENERGY_EXPORT}
    if info["relay"]:
        caps.add(Cap.RELAY)
    if info["gas"]:
        caps.add(Cap.GAS)
    return caps


MAX_CLOCK_SKEW = timedelta(hours=1)


def local_candidates(naive: datetime, tz: ZoneInfo) -> list[datetime]:
    """UTC instants a zone-less local time can mean (two in the autumn double hour)."""
    out: list[datetime] = []
    for fold in (0, 1):
        c = naive.replace(tzinfo=tz, fold=fold).astimezone(UTC)
        if c not in out:
            out.append(c)
    return out


def resolve_device_time(
    value: datetime,
    tz: ZoneInfo,
    received_at: datetime | None,
    *,
    fallback: bool = True,
    max_skew: timedelta = MAX_CLOCK_SKEW,
) -> datetime:
    """UTC time for a device timestamp.

    Zone-less values: the candidate closest to `received_at`. With `fallback`, a result more than
    `max_skew` away from `received_at` is replaced by `received_at` (device clock wrong or
    ambiguous beyond repair). Without `fallback` (meter readings that are legitimately old, such
    as gas) only a timestamp in the future beyond `max_skew` is replaced.
    """
    cands = [value.astimezone(UTC)] if value.tzinfo else local_candidates(value, tz)
    if received_at is None:
        return cands[0]
    best = min(cands, key=lambda c: abs(c - received_at))
    off = best - received_at
    if (fallback and abs(off) > max_skew) or off > max_skew:
        return received_at
    return best


def parse_gas_timestamp_v1(
    value: int | str | None, tz: ZoneInfo, received_at: datetime | None = None
) -> datetime | None:
    """v1 gas_timestamp is YYMMDDhhmmss in local time (no zone)."""
    if value is None:
        return None
    s = str(value).zfill(12)
    try:
        local = datetime(2000 + int(s[0:2]), int(s[2:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]), int(s[10:12]))
    except ValueError:
        return None
    return resolve_device_time(local, tz, received_at, fallback=False)


def _parse_device_iso(
    value: str | None, tz: ZoneInfo, received_at: datetime | None, *, fallback: bool = True
) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return resolve_device_time(dt, tz, received_at, fallback=fallback)


def _reading(serial: str, metric: str, value: Any, unit: str, at: datetime, raw: dict | None = None) -> Reading:
    quality = Quality.MEASURED if value is not None else Quality.MISSING
    return Reading(
        device_identity=serial,
        metric=metric,
        value=float(value) if value is not None else None,
        unit=unit,
        observed_at=at,
        quality=quality,
        event_id=f"homewizard:{serial}:{metric}:{at.isoformat()}",
        raw=raw,
    )


def normalize_v1(serial: str, data: dict[str, Any], at: datetime, tz: ZoneInfo) -> list[Reading]:
    out = [_reading(serial, m, data[f], u, at) for f, (m, u) in V1_FIELDS.items() if f in data]
    if "total_gas_m3" in data:
        gas_at = parse_gas_timestamp_v1(data.get("gas_timestamp"), tz, at) or at
        out.append(_reading(serial, "gas_m3", data["total_gas_m3"], "m3", gas_at))
    return out


def normalize_v2(serial: str, data: dict[str, Any], polled_at: datetime, tz: ZoneInfo) -> list[Reading]:
    """`polled_at` is the receive time (UTC); it resolves zone-less device timestamps."""
    at = _parse_device_iso(data.get("timestamp"), tz, polled_at) or polled_at
    out = [_reading(serial, m, data[f], u, at) for f, (m, u) in V2_FIELDS.items() if f in data]
    for ext in data.get("external", []) or []:
        if ext.get("type") == "gas_meter":
            ext_at = _parse_device_iso(ext.get("timestamp"), tz, polled_at, fallback=False) or at
            out.append(_reading(serial, "gas_m3", ext.get("value"), "m3", ext_at))
    return out


class HomeWizardAdapter(Adapter):
    provider = "homewizard"

    def __init__(
        self,
        config: dict[str, Any],
        credentials: dict[str, str] | None = None,
        *,
        relay_allowed: Callable[[str], bool] | None = None,
        http_factory: Callable[[str, str | None], httpx.AsyncClient] | None = None,
        fingerprint_fetcher: Callable[[str], Any] = fetch_fingerprint,
    ):
        self.config = dict(config)
        self.credentials = dict(credentials or {})
        # Policy hook. Default: deny. The API wires this to its policy engine.
        self._relay_allowed = relay_allowed or (lambda identity: False)
        self._factory = http_factory or (lambda base, fp: client_for(base, fp))
        self._fetch_fp = fingerprint_fetcher
        self._tz = ZoneInfo(self.config.get("timezone", "Europe/Amsterdam"))
        self._client: httpx.AsyncClient | None = None
        self._breaker = CircuitBreaker(threshold=5, reset_after=30)
        self._poll_limiter = RateLimiter(float(self.config.get("min_poll_interval_s", 1.0)))
        self._cmd_limiter = RateLimiter(2.0)
        self._identity_ok: bool | None = None
        self._identity_checked_at: datetime | None = None
        self._found_serial: str | None = None
        self._last_ok_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def host(self) -> str:
        host = self.config.get("host")
        if not host:
            raise AdapterError("Geen IP-adres voor het HomeWizard-apparaat ingesteld")
        return host

    @property
    def api_version(self) -> str:
        return self.config.get("api_version", "v1")

    @property
    def serial(self) -> str:
        return self.config.get("serial") or self.host

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            if self.api_version == "v2":
                self._client = self._factory(f"https://{self.host}", self.credentials.get("cert_fingerprint"))
            else:
                self._client = self._factory(f"http://{self.host}", None)
        return self._client

    def _headers(self) -> dict[str, str]:
        if self.api_version != "v2":
            return {}
        token = self.credentials.get("token")
        if not token:
            raise PairingRequired("HomeWizard v2 is nog niet gekoppeld")
        return {"Authorization": f"Bearer {token}", "X-Api-Version": "2"}

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        async def go():
            try:
                r = await self._http().request(method, path, headers=self._headers(), **kw)
            except httpx.TransportError as exc:
                raise DeviceOffline(
                    f"HomeWizard {self.host} niet bereikbaar: {network_reason(exc)}",
                    diagnostics={"host": self.host, "error": f"{type(exc).__name__}: {exc}"[:300]},
                ) from exc
            if r.status_code == 401:
                raise PairingRequired("HomeWizard weigert de token; koppel opnieuw")
            if r.status_code == 403:
                raise AdapterError("Lokale API staat uit in de HomeWizard Energy-app")
            r.raise_for_status()
            return r.json()

        return await self._breaker.call(go)

    # -- contract ---------------------------------------------------------
    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredDevice]:
        """mDNS `_hwenergy._tcp` (v1) and `_homewizard._tcp` (v2). [] without zeroconf."""
        try:
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
        except ImportError:
            return []
        types = ["_hwenergy._tcp.local.", "_homewizard._tcp.local."]
        azc = AsyncZeroconf()
        seen: list[tuple[str, str]] = []

        def on_change(zeroconf, service_type, name, state_change):
            if state_change is ServiceStateChange.Added:
                seen.append((service_type, name))

        browser = AsyncServiceBrowser(azc.zeroconf, types, handlers=[on_change])
        found: dict[str, DiscoveredDevice] = {}
        try:
            await asyncio.sleep(timeout_s)
            for stype, name in seen:
                info = AsyncServiceInfo(stype, name)
                if not await info.async_request(azc.zeroconf, 2000):
                    continue
                props = {k.decode(): (v or b"").decode() for k, v in (info.properties or {}).items()}
                serial = props.get("serial") or name
                addrs = info.parsed_addresses()
                ptype = props.get("product_type")
                found[serial] = DiscoveredDevice(
                    provider="homewizard",
                    stable_identity=serial,
                    kind=product_info(ptype)["kind"],
                    name=props.get("product_name", name),
                    host=addrs[0] if addrs else None,
                    model=ptype,
                    extra={"api_v2": stype.startswith("_homewizard")},
                )
        finally:
            await browser.async_cancel()
            await azc.async_close()
        return list(found.values())

    async def probe(self, host: str) -> DiscoveredDevice:
        try:
            async with self._factory(f"http://{host}", None) as c:
                r = await c.get("/api")
        except httpx.TransportError as exc:
            raise DeviceOffline(f"Geen HomeWizard-apparaat bereikbaar op {host}") from exc
        if r.status_code == 403:
            raise AdapterError("Lokale API staat uit: zet ‘Lokale API’ aan in de HomeWizard Energy-app")
        r.raise_for_status()
        info = r.json()
        ptype = info.get("product_type")
        pinfo = product_info(ptype)
        return DiscoveredDevice(
            provider="homewizard",
            stable_identity=str(info.get("serial", host)),
            kind=pinfo["kind"],
            name=info.get("product_name", pinfo["label"]),
            host=host,
            model=ptype,
            firmware=info.get("firmware_version"),
            extra={"api_version": info.get("api_version"), "v2_capable": pinfo["v2"]},
        )

    async def verify_identity(self) -> dict[str, Any]:
        """GET /api and compare the serial with the paired one. Raises IdentityMismatch."""
        info = await self._request("GET", "/api")
        found = str(info.get("serial") or "")
        expected = str(self.config.get("serial") or "")
        self._found_serial = found or None
        self._identity_checked_at = datetime.now(UTC)
        if not expected:
            self._identity_ok = None  # nothing to compare with (legacy configuration)
            return info
        if found.lower() != expected.lower():
            self._identity_ok = False
            raise IdentityMismatch(
                f"Op {self.host} antwoordt een ander HomeWizard-apparaat (serienummer {found or 'onbekend'}, "
                f"verwacht {expected}). Waarschijnlijk is het IP-adres veranderd: stel het juiste adres in "
                "of koppel opnieuw.",
                diagnostics={
                    "host": self.host,
                    "expected_serial": expected,
                    "found_serial": found or None,
                    "found_product_type": info.get("product_type"),
                },
            )
        self._identity_ok = True
        return info

    async def _identity_due(self) -> None:
        every = float(self.config.get("identity_check_interval_s", 0))
        last = self._identity_checked_at
        if self._identity_ok is not True or last is None or every <= 0:
            await self.verify_identity()
        elif (datetime.now(UTC) - last).total_seconds() >= every:
            await self.verify_identity()

    async def pair(self, host: str, **kwargs: Any) -> PairResult | PairingInstructions:
        dev = await self.probe(host)
        expected = kwargs.get("expected_serial")
        if expected and str(expected).lower() != dev.stable_identity.lower():
            raise IdentityMismatch(
                f"Op {host} antwoordt een ander HomeWizard-apparaat (serienummer {dev.stable_identity}, "
                f"verwacht {expected}).",
                diagnostics={"host": host, "expected_serial": expected, "found_serial": dev.stable_identity},
            )
        base_cfg = {"host": host, "serial": dev.stable_identity, "product_type": dev.model}
        v2_ok = False
        if kwargs.get("prefer_v2", True) and dev.extra.get("v2_capable"):
            try:
                fp = await self._fetch_fp(host)
                async with self._factory(f"https://{host}", fp) as c:
                    r = await c.post("/api/user", json={"name": USER_NAME}, headers={"X-Api-Version": "2"})
                v2_ok = True
            except (OSError, httpx.TransportError) as exc:
                # Older firmware has no HTTPS v2 endpoint: fall back to the v1 API.
                log.info("HomeWizard %s: API v2 niet bereikbaar (%s); v1 wordt gebruikt", host, exc)
        if v2_ok:
            if r.status_code == 403:
                return INSTRUCTIONS
            r.raise_for_status()
            token = r.json().get("token")
            if not token:
                raise AdapterError("Geen token ontvangen van HomeWizard")
            creds = {"token": token, "cert_fingerprint": fp}
            self.config.update(base_cfg, api_version="v2")
        else:
            creds = {}
            self.config.update(base_cfg, api_version="v1")
        self.credentials = creds
        self._client = None
        dev.extra["api_version_used"] = self.config["api_version"]
        return PairResult(stable_identity=dev.stable_identity, credentials=creds, device=dev)

    def instructions(self) -> PairingInstructions:
        return INSTRUCTIONS

    async def get_capabilities(self, device_identity: str) -> set[Cap]:
        return capabilities_for(self.config.get("product_type"))

    async def _relay_state(self) -> dict[str, Any]:
        """relay_on / switch_lock of an Energy Socket. Read only: switching stays policy-gated."""
        if self.api_version == "v2":
            # API v2 has no documented socket-state endpoint yet (Energy Socket v2 is "in development").
            # The v1 state endpoint stays available while "api_v1_enabled" is on; it needs no token,
            # so none is sent. Without it the state is reported as unsupported, never guessed.
            try:
                async with self._factory(f"http://{self.host}", None) as c:
                    r = await c.get("/api/v1/state")
                state = r.json() if r.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                state = None
            if not isinstance(state, dict):
                return {"relay_on": None, "switch_lock": None, "relay_state": "unsupported"}
        else:
            state = await self._request("GET", "/api/v1/state")
        out: dict[str, Any] = {
            "relay_on": state.get("power_on"),
            "switch_lock": state.get("switch_lock"),
            "relay_state": "ok",
        }
        if "brightness" in state:
            out["led_brightness"] = state["brightness"]
        return out

    async def poll(self) -> list[Event]:
        try:
            return await self._poll()
        except (AdapterError, httpx.HTTPError) as exc:
            self._last_error = str(exc)
            raise

    async def _poll(self) -> list[Event]:
        await self._poll_limiter.wait()
        now = datetime.now(UTC).replace(microsecond=0)
        await self._identity_due()  # never store readings of another device under this serial
        events: list[Event] = []
        if self.api_version == "v2":
            data = await self._request("GET", "/api/measurement")
            events.extend(normalize_v2(self.serial, data, now, self._tz))
        else:
            data = await self._request("GET", "/api/v1/data")
            events.extend(normalize_v1(self.serial, data, now, self._tz))
        if product_info(self.config.get("product_type"))["relay"]:
            events.append(StateUpdate(self.serial, await self._relay_state(), now))
        self._last_ok_at, self._last_error = datetime.now(UTC), None
        return events

    async def execute(self, command: Command) -> CommandResult:
        if command.action != "set_relay":
            raise Unsupported(f"HomeWizard ondersteunt actie '{command.action}' niet")
        if Cap.RELAY not in capabilities_for(self.config.get("product_type")):
            raise Unsupported("Dit HomeWizard-apparaat heeft geen schakelaar")
        if not self._relay_allowed(command.device_identity):
            raise PolicyDenied(
                "Schakelen van deze stekker is geblokkeerd: hij is ingesteld als alleen-meten (bijv. boiler)."
            )
        await self._cmd_limiter.wait(command.device_identity)
        want = bool(command.params["on"])
        await self._request("PUT", "/api/v1/state", json={"power_on": want})
        back = await self._request("GET", "/api/v1/state")
        confirmed = back.get("power_on") == want
        detail = "" if confirmed else "Stekker bevestigt de gevraagde stand niet (readback wijkt af)"
        return CommandResult(ok=True, confirmed=confirmed, detail=detail, state=back)

    def _health_extra(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version if self.config.get("host") else None,
            "serial": self.config.get("serial"),
            "found_serial": self._found_serial,
            "product_type": self.config.get("product_type"),
            "identity_verified": self._identity_ok,
            "identity_checked_at": self._identity_checked_at.isoformat() if self._identity_checked_at else None,
            "last_ok_at": self._last_ok_at.isoformat() if self._last_ok_at else None,
            "last_error": self._last_error,
            "breaker": self._breaker.state,
        }

    async def health(self) -> Health:
        if not self.config.get("host"):
            return Health(HealthState.NOT_CONFIGURED, "Geen apparaat ingesteld", extra=self._health_extra())
        try:
            await self.verify_identity()
            path = "/api/measurement" if self.api_version == "v2" else "/api/v1/data"
            await self._request("GET", path)
        except AdapterError as exc:
            self._last_error = str(exc)
            return Health(HealthState.ERROR, str(exc), code=exc.code, extra=self._health_extra())
        except httpx.HTTPError as exc:
            self._last_error = f"HTTP-fout: {exc}"
            return Health(HealthState.ERROR, self._last_error, code="http_error", extra=self._health_extra())
        self._last_ok_at, self._last_error = datetime.now(UTC), None
        checked = ", serienummer gecontroleerd" if self._identity_ok else ""
        return Health(HealthState.OK, f"Verbonden (API {self.api_version}{checked})", extra=self._health_extra())

    def diagnostics(self) -> dict[str, Any]:
        return {"host": self.config.get("host"), **self._health_extra()}

    async def unpair(self, device_identity: str) -> None:
        if self.api_version == "v2" and self.credentials.get("token"):
            try:
                await self._request("DELETE", "/api/user", json={"name": USER_NAME})
            except (AdapterError, httpx.HTTPError):
                log.info("HomeWizard: token intrekken mislukt; verwijder hem handmatig in de app")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
