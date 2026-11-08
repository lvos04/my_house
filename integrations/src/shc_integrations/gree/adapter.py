"""Gree air conditioner adapter (local UDP, no cloud, no Home Assistant).

Compatibility is never assumed: every unit is reported as `untested` with
diagnostics until verified on real hardware. A command only counts as
`confirmed` when a status readback shows the requested values.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ..base import (
    Adapter,
    AdapterError,
    Cap,
    Command,
    CommandResult,
    Compatibility,
    DeviceOffline,
    DiscoveredDevice,
    Event,
    Health,
    HealthState,
    PairingInstructions,
    PairingRequired,
    PairResult,
    Quality,
    Reading,
    StateUpdate,
    Unsupported,
)
from ..resilience import CircuitBreaker, RateLimiter, retry
from . import protocol
from .protocol import GENERIC_KEY_V1, GENERIC_KEY_V2, GreeProtocolError

log = logging.getLogger(__name__)

MODES = {0: "auto", 1: "cool", 2: "dry", 3: "fan", 4: "heat"}
MODES_REV = {v: k for k, v in MODES.items()}
FAN = {0: "auto", 1: "low", 2: "low", 3: "medium", 4: "high", 5: "high"}
FAN_REV = {"auto": 0, "low": 1, "medium": 3, "high": 5}
STATUS_COLS = ["Pow", "Mod", "SetTem", "TemUn", "WdSpd", "SwUpDn", "TemSen", "TemRec", "Lig", "Quiet", "Tur"]
MIN_C, MAX_C = 16, 30

INSTRUCTIONS = PairingInstructions(
    steps=[
        "Installeer de Gree+-app en maak een account (alleen nodig voor de eerste wifi-koppeling).",
        "Zet de binnenunit in koppelmodus: houd op de afstandsbediening ‘Mode’ en ‘Wifi’ tegelijk "
        "ongeveer 2 seconden ingedrukt, of volg de handleiding van je model.",
        "Koppel de unit in de Gree+-app aan je 2,4 GHz-wifi (5 GHz werkt niet).",
        "Geef de unit in je router een vast IP-adres (DHCP-reservering) en vul dat hier in.",
        "Klik op ‘Zoeken’ of ‘Koppelen’. De app test dan of de lokale besturing met jouw model werkt.",
        "Werkt het niet? Dan blijft de unit ‘niet ondersteund’ en kun je de gewone afstandsbediening gebruiken.",
    ]
)

Requester = Callable[[str, dict[str, Any], int, float], Awaitable[dict[str, Any]]]


def decode_temperature(raw: Any, offset: str | int = "auto") -> float | None:
    """TemSen is reported with a +40 offset by many firmwares. Returns None when implausible."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if offset == "auto":
        if value >= 40:
            value -= 40
    else:
        value -= float(offset)
    if not -15 <= value <= 50:
        return None
    return value


def decode_status(cols: list[str], dat: list[Any], offset: str | int = "auto") -> dict[str, Any]:
    raw = dict(zip(cols, dat, strict=False))
    state: dict[str, Any] = {"raw": raw}
    if "Pow" in raw:
        state["power"] = bool(raw["Pow"])
    if "Mod" in raw:
        state["mode"] = MODES.get(raw["Mod"], f"unknown_{raw['Mod']}")
    if "SetTem" in raw:
        t = float(raw["SetTem"])
        if raw.get("TemRec") == 1:
            t += 0.5
        if raw.get("TemUn", 0) == 1:  # Fahrenheit display; SetTem is still Celsius on most units
            state["display_unit"] = "F"
        state["target_c"] = t
    if "WdSpd" in raw:
        state["fan"] = FAN.get(raw["WdSpd"], "auto")
    if "SwUpDn" in raw:
        state["swing"] = raw["SwUpDn"] != 0
    if "TemSen" in raw:
        state["indoor_c"] = decode_temperature(raw["TemSen"], offset)
    return state


def encode_command(params: dict[str, Any]) -> tuple[list[str], list[int]]:
    opt: list[str] = []
    p: list[int] = []
    if "power" in params:
        opt.append("Pow")
        p.append(1 if params["power"] else 0)
    if params.get("mode") is not None:
        if params["mode"] not in MODES_REV:
            raise AdapterError(f"Onbekende modus '{params['mode']}'")
        opt.append("Mod")
        p.append(MODES_REV[params["mode"]])
    if params.get("target_c") is not None:
        t = float(params["target_c"])
        if not MIN_C <= t <= MAX_C:
            raise AdapterError(f"Doeltemperatuur moet tussen {MIN_C} en {MAX_C} °C liggen")
        whole = int(t)
        opt += ["TemUn", "SetTem", "TemRec"]
        p += [0, whole, 1 if t - whole >= 0.5 else 0]
    if params.get("fan") is not None:
        if params["fan"] not in FAN_REV:
            raise AdapterError(f"Onbekende ventilatorstand '{params['fan']}'")
        opt.append("WdSpd")
        p.append(FAN_REV[params["fan"]])
    if params.get("swing") is not None:
        opt.append("SwUpDn")
        p.append(1 if params["swing"] else 0)
    if not opt:
        raise AdapterError("Leeg airco-commando")
    return opt, p


def _matches(requested: dict[str, Any], state: dict[str, Any]) -> bool:
    for key, want in requested.items():
        if key == "target_c":
            if state.get("target_c") is None or abs(state["target_c"] - float(want)) > 0.25:
                return False
        elif key == "power":
            if state.get("power") != bool(want):
                return False
        elif state.get(key) != want:
            return False
    return True


class GreeAdapter(Adapter):
    """One instance per indoor unit."""

    provider = "gree"

    def __init__(
        self,
        config: dict[str, Any],
        credentials: dict[str, str] | None = None,
        *,
        requester: Requester | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        min_command_interval_s: float = 2.0,
    ):
        self.config = dict(config)
        self.credentials = dict(credentials or {})
        self._request: Requester = requester or (
            lambda host, payload, port, timeout: protocol.request(host, payload, port, timeout)
        )
        self._sleep = sleep
        self._breaker = CircuitBreaker(threshold=4, reset_after=60)
        self._limiter = RateLimiter(min_command_interval_s, sleep=sleep)
        self._lock = asyncio.Lock()
        self._last_state: dict[str, Any] | None = None
        self.last_diagnostics: dict[str, Any] = {}

    @property
    def host(self) -> str:
        host = self.config.get("host")
        if not host:
            raise AdapterError("Geen IP-adres voor de Gree-unit ingesteld")
        return host

    @property
    def port(self) -> int:
        return int(self.config.get("port", protocol.DEFAULT_PORT))

    @property
    def mac(self) -> str:
        return self.config.get("mac", "")

    @property
    def version(self) -> str:
        return self.config.get("encryption", "v1")

    def _key(self) -> bytes:
        key = self.credentials.get("key")
        if not key:
            raise PairingRequired("Gree-unit is nog niet gebonden")
        return key.encode()

    async def _roundtrip(self, inner: dict[str, Any], key: bytes, version: str, i: int = 0) -> dict[str, Any]:
        pkt = protocol.build_packet(inner, key, version, tcid=self.mac, i=i)
        timeout = float(self.config.get("timeout_s", 3.0))

        async def once():
            try:
                outer = await self._request(self.host, pkt, self.port, timeout)
            except (TimeoutError, OSError) as exc:
                raise DeviceOffline(f"Gree-unit {self.host} reageert niet") from exc
            return protocol.open_packet(outer, key)

        return await self._breaker.call(
            lambda: retry(once, attempts=3, base_delay=0.5, retry_on=(DeviceOffline,), sleep=self._sleep)
        )

    # -- contract ---------------------------------------------------------
    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredDevice]:
        replies = await protocol.broadcast(
            {"t": "scan"}, self.config.get("broadcast", "255.255.255.255"), self.port, timeout_s
        )
        out: dict[str, DiscoveredDevice] = {}
        for outer, addr in replies:
            try:
                dev = self._device_from_scan(outer, addr[0])
            except GreeProtocolError:
                continue
            out[dev.stable_identity] = dev
        return list(out.values())

    def _device_from_scan(self, outer: dict[str, Any], host: str) -> DiscoveredDevice:
        version = "v2" if "tag" in outer else "v1"
        key = GENERIC_KEY_V2 if version == "v2" else GENERIC_KEY_V1
        info = protocol.open_packet(outer, key)
        mac = info.get("mac") or info.get("cid") or outer.get("cid")
        if not mac:
            raise GreeProtocolError("Scan-antwoord zonder MAC")
        diag = {k: info.get(k) for k in ("mid", "model", "ver", "brand", "series", "name", "subCnt")}
        return DiscoveredDevice(
            provider="gree",
            stable_identity=str(mac),
            kind="ac_unit",
            name=info.get("name") or f"Gree {mac[-4:]}",
            host=host,
            model=info.get("model"),
            firmware=info.get("ver"),
            extra={"encryption": version, "diagnostics": diag, "compatibility": Compatibility.UNTESTED.value},
        )

    async def probe(self, host: str) -> DiscoveredDevice:
        try:
            outer = await self._request(host, {"t": "scan"}, self.port, float(self.config.get("timeout_s", 3.0)))
        except (TimeoutError, OSError) as exc:
            raise DeviceOffline(
                f"Geen antwoord van {host} op UDP-poort {self.port}. Is de unit via Gree+ met wifi verbonden?"
            ) from exc
        dev = self._device_from_scan(outer, host)
        self.last_diagnostics = dev.extra["diagnostics"]
        return dev

    async def pair(self, host: str, **kwargs: Any) -> PairResult | PairingInstructions:
        dev = await self.probe(host)
        version = dev.extra["encryption"]
        generic = GENERIC_KEY_V2 if version == "v2" else GENERIC_KEY_V1
        self.config.update(host=host, mac=dev.stable_identity, encryption=version)
        res = await self._roundtrip({"mac": dev.stable_identity, "t": "bind", "uid": 0}, generic, version, i=1)
        if res.get("t") != "bindok" or not res.get("key"):
            raise AdapterError(f"Binden mislukt (antwoord: {res.get('t')}). Model mogelijk niet ondersteund.")
        self.credentials = {"key": res["key"]}
        # Compatibility gate: a real status read must work before we call it paired.
        try:
            await self.read_status()
        except (AdapterError, GreeProtocolError) as exc:
            raise AdapterError(f"Gebonden, maar status lezen mislukt: {exc}. Unit blijft ‘niet ondersteund’.") from exc
        return PairResult(stable_identity=dev.stable_identity, credentials=dict(self.credentials), device=dev)

    def instructions(self) -> PairingInstructions:
        return INSTRUCTIONS

    async def get_capabilities(self, device_identity: str) -> set[Cap]:
        caps = {Cap.POWER, Cap.HVAC_MODE, Cap.SETPOINT, Cap.FAN}
        raw = (self._last_state or {}).get("raw", {})
        if "SwUpDn" in raw:
            caps.add(Cap.SWING)
        if (self._last_state or {}).get("indoor_c") is not None:
            caps.add(Cap.TEMPERATURE)
        return caps

    async def read_status(self, cols: list[str] | None = None) -> dict[str, Any]:
        cols = cols or STATUS_COLS
        res = await self._roundtrip({"cols": cols, "mac": self.mac, "t": "status"}, self._key(), self.version)
        if res.get("t") != "dat":
            raise AdapterError(f"Onverwacht statusantwoord: {res.get('t')}")
        state = decode_status(res.get("cols", cols), res.get("dat", []), self.config.get("temsen_offset", "auto"))
        self._last_state = state
        return state

    async def poll(self) -> list[Event]:
        now = datetime.now(UTC)
        state = await self.read_status()
        events: list[Event] = [StateUpdate(self.mac, {k: v for k, v in state.items() if k != "raw"}, now)]
        if "indoor_c" in state:
            q = Quality.MEASURED if state["indoor_c"] is not None else Quality.MISSING
            events.append(
                Reading(self.mac, "unit_temperature", state["indoor_c"], "°C", now, quality=q, raw=state["raw"])
            )
        return events

    async def execute(self, command: Command) -> CommandResult:
        if command.action != "set_hvac":
            raise Unsupported(f"Gree ondersteunt actie '{command.action}' niet")
        params = {k: v for k, v in command.params.items() if v is not None}
        if params.get("swing") is not None and Cap.SWING not in await self.get_capabilities(self.mac):
            raise Unsupported("Deze unit meldt geen swing-ondersteuning")
        opt, p = encode_command(params)
        async with self._lock:
            await self._limiter.wait(self.mac)
            res = await self._roundtrip({"opt": opt, "p": p, "t": "cmd"}, self._key(), self.version)
            if res.get("t") != "res" or res.get("r", 200) != 200:
                return CommandResult(ok=False, confirmed=False, detail=f"Unit weigerde commando ({res.get('r')})")
            state = await self.read_status()
        confirmed = _matches(params, state)
        detail = "" if confirmed else "Unit bevestigt de gevraagde stand niet (readback wijkt af)"
        return CommandResult(
            ok=True, confirmed=confirmed, detail=detail, state={k: v for k, v in state.items() if k != "raw"}
        )

    async def health(self) -> Health:
        if not self.credentials.get("key"):
            return Health(HealthState.NOT_CONFIGURED, "Nog niet gekoppeld")
        try:
            await self.read_status(["Pow"])
        except (AdapterError, GreeProtocolError) as exc:
            return Health(HealthState.ERROR, str(exc))
        return Health(HealthState.OK, "Bereikbaar (compatibiliteit: nog niet getest met echte hardware)")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "host": self.config.get("host"),
            "mac": self.mac,
            "encryption": self.version,
            "breaker": self._breaker.state,
            "device": self.last_diagnostics,
            "last_raw_status": (self._last_state or {}).get("raw"),
            "compatibility": Compatibility.UNTESTED.value,
        }
