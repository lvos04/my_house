"""Gree air conditioner adapter (local UDP, no cloud, no Home Assistant).

Compatibility is never assumed: every unit is reported as `untested` with
diagnostics until verified on real hardware. A command only counts as
`confirmed` when a status readback shows the requested values.

Protocol notes (aligned with the reference library greeclimate):
- `TemSen` (room temperature at the unit) is reported with a +40 offset by most
  firmwares; 0 means the unit has no (working) sensor.
- `SetTem` is the target in whole °C. `TemRec` is the Fahrenheit rounding bit and is
  NOT +0.5 °C; only units verified to take half degrees (config `half_degrees`)
  use it for 0.5 °C steps.
- `SwUpDn` 1 and 7-11 are swing patterns; 2-6 are FIXED louver positions (no swing).
- Binding: try the old (v1, AES-ECB) generic key first, then the new one (v2, AES-GCM).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
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
CAP_MODES = ["off", "auto", "cool", "dry", "fan", "heat"]
FAN = {0: "auto", 1: "low", 2: "low", 3: "medium", 4: "high", 5: "high"}
FAN_REV = {"auto": 0, "low": 1, "medium": 3, "high": 5}
LOUVER = {
    0: "default",
    1: "swing_full",
    2: "fixed_upper",
    3: "fixed_upper_middle",
    4: "fixed_middle",
    5: "fixed_lower_middle",
    6: "fixed_lower",
    7: "swing_lower",
    8: "swing_lower_middle",
    9: "swing_middle",
    10: "swing_upper_middle",
    11: "swing_upper",
}
SWINGING = frozenset({1, 7, 8, 9, 10, 11})
STATUS_COLS = ["Pow", "Mod", "SetTem", "TemUn", "WdSpd", "SwUpDn", "TemSen", "TemRec", "Lig", "Quiet", "Tur"]
SCAN_FIELDS = ("mac", "cid", "mid", "model", "ver", "brand", "series", "name", "subCnt", "vender", "lock", "hid")
BIND_ORDER = ("v1", "v2")
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
    """Room temperature from TemSen. None when the unit has no sensor (0) or the value is implausible."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None  # 0 = no (working) room sensor; never show it as 0 °C
    if offset == "auto":
        if value >= 40:
            value -= 40
    else:
        value -= float(offset)
    if not -15 <= value <= 50:
        return None
    return value


def decode_status(
    cols: list[str], dat: list[Any], offset: str | int = "auto", half_degrees: bool = False
) -> dict[str, Any]:
    raw = dict(zip(cols, dat, strict=False))
    state: dict[str, Any] = {"raw": raw}
    if "Pow" in raw:
        state["power"] = bool(raw["Pow"])
    if "Mod" in raw:
        state["mode"] = MODES.get(raw["Mod"], f"unknown_{raw['Mod']}")
    if "SetTem" in raw:
        t = float(raw["SetTem"])
        fahrenheit = raw.get("TemUn", 0) == 1
        if half_degrees and not fahrenheit and raw.get("TemRec") == 1:
            t += 0.5
        if fahrenheit:  # display in °F; SetTem stays °C and TemRec is the °F rounding bit
            state["display_unit"] = "F"
        state["target_c"] = t
    if "WdSpd" in raw:
        state["fan"] = FAN.get(raw["WdSpd"], "auto")
    if "SwUpDn" in raw:
        state["swing"] = raw["SwUpDn"] in SWINGING
        state["louver"] = LOUVER.get(raw["SwUpDn"], f"unknown_{raw['SwUpDn']}")
    if "TemSen" in raw:
        state["indoor_sensor"] = raw["TemSen"] not in (0, None)
        state["indoor_c"] = decode_temperature(raw["TemSen"], offset)
    return state


def encode_command(params: dict[str, Any], half_degrees: bool = False) -> tuple[list[str], list[int]]:
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
        if half_degrees:
            if t * 2 != int(t * 2):
                raise AdapterError("Deze unit accepteert stappen van 0,5 °C")
            opt += ["TemUn", "SetTem", "TemRec"]
            p += [0, whole, 1 if t - whole >= 0.5 else 0]
        else:
            if t != whole:
                raise AdapterError("Deze unit accepteert alleen hele graden (stap 1 °C)")
            opt += ["TemUn", "SetTem"]
            p += [0, whole]
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
        elif key in ("power", "swing"):
            if state.get(key) != bool(want):
                return False
        elif state.get(key) != want:
            return False
    return True


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


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
        self._last_ok_at: datetime | None = None
        self._last_error: str | None = None
        self._last_error_at: datetime | None = None
        self._failures = 0

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

    @property
    def half_degrees(self) -> bool:
        return bool(self.config.get("half_degrees", False))

    @property
    def timeout(self) -> float:
        return float(self.config.get("timeout_s", 3.0))

    def _key(self) -> bytes:
        key = self.credentials.get("key")
        if not key:
            raise PairingRequired("Gree-unit is nog niet gebonden")
        return key.encode()

    async def _roundtrip(
        self,
        inner: dict[str, Any],
        key: bytes,
        version: str,
        i: int = 0,
        *,
        host: str | None = None,
        attempts: int = 3,
        use_breaker: bool = True,
    ) -> dict[str, Any]:
        target = host or self.host
        pkt = protocol.build_packet(inner, key, version, tcid=self.mac, i=i)

        async def once():
            try:
                outer = await self._request(target, pkt, self.port, self.timeout)
            except (TimeoutError, OSError) as exc:
                raise DeviceOffline(f"Gree-unit {target} reageert niet") from exc
            return protocol.open_packet(outer, key)

        def go():
            return retry(once, attempts=attempts, base_delay=0.5, retry_on=(DeviceOffline,), sleep=self._sleep)

        return await (self._breaker.call(go) if use_breaker else go())

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
        # The scan answer format (tag = GCM) only tells how the SCAN was encrypted.
        scan_version = "v2" if "tag" in outer else "v1"
        key = GENERIC_KEY_V2 if scan_version == "v2" else GENERIC_KEY_V1
        info = protocol.open_packet(outer, key)
        mac = info.get("mac") or info.get("cid") or outer.get("cid")
        if not mac:
            raise GreeProtocolError("Scan-antwoord zonder MAC")
        diag = {k: info.get(k) for k in SCAN_FIELDS if info.get(k) is not None}
        diag["mac"] = str(mac)
        return DiscoveredDevice(
            provider="gree",
            stable_identity=str(mac),
            kind="ac_unit",
            name=info.get("name") or f"Gree {str(mac)[-4:]}",
            host=host,
            model=info.get("model"),
            firmware=info.get("ver"),
            extra={
                "scan_encryption": scan_version,
                "diagnostics": diag,
                "compatibility": Compatibility.UNTESTED.value,
            },
        )

    async def probe(self, host: str) -> DiscoveredDevice:
        self.last_diagnostics = {"host": host, "port": self.port, "stage": "scan"}

        async def scan():
            try:
                return await self._request(host, {"t": "scan"}, self.port, self.timeout)
            except (TimeoutError, OSError) as exc:
                raise DeviceOffline(
                    f"Geen antwoord van {host} op UDP-poort {self.port}. Is de unit via Gree+ met wifi verbonden?"
                ) from exc

        try:
            outer = await retry(scan, attempts=2, base_delay=0.5, retry_on=(DeviceOffline,), sleep=self._sleep)
        except DeviceOffline as exc:
            self.last_diagnostics.update(stage="scan_failed", error="Geen antwoord op scan")
            raise DeviceOffline(str(exc), diagnostics=self.diagnostics()) from exc
        try:
            dev = self._device_from_scan(outer, host)
        except GreeProtocolError as exc:
            self.last_diagnostics.update(
                stage="scan_unreadable",
                error=str(exc),
                scan_keys=sorted(outer) if isinstance(outer, dict) else None,
            )
            raise GreeProtocolError(f"Onleesbaar scan-antwoord: {exc}", diagnostics=self.diagnostics()) from exc
        self.last_diagnostics.update(
            stage="scanned",
            scan=dev.extra["diagnostics"],
            scan_encryption=dev.extra["scan_encryption"],
            mac=dev.stable_identity,
            model=dev.model,
            firmware=dev.firmware,
        )
        return dev

    async def _bind(self, host: str, mac: str, order: tuple[str, ...]) -> tuple[str, str] | None:
        """Try the generic keys in `order`; returns (device key, encryption) or None."""
        attempts: list[dict[str, Any]] = []
        self.last_diagnostics["bind_attempts"] = attempts
        for version in order:
            generic = GENERIC_KEY_V2 if version == "v2" else GENERIC_KEY_V1
            try:
                res = await self._roundtrip(
                    {"mac": mac, "t": "bind", "uid": 0}, generic, version, i=1, host=host, attempts=2, use_breaker=False
                )
            except DeviceOffline:
                attempts.append({"encryption": version, "result": "geen antwoord"})
                continue
            except GreeProtocolError as exc:
                attempts.append({"encryption": version, "result": "onleesbaar antwoord", "detail": str(exc)})
                continue
            if res.get("t") == "bindok" and res.get("key"):
                attempts.append({"encryption": version, "result": "ok"})
                return str(res["key"]), version
            attempts.append({"encryption": version, "result": f"geweigerd ({res.get('t')})"})
        return None

    async def pair(self, host: str, **kwargs: Any) -> PairResult | PairingInstructions:
        dev = await self.probe(host)
        mac = dev.stable_identity
        self.config.update(host=host, mac=mac)
        forced = kwargs.get("encryption")
        order = BIND_ORDER if forced in (None, "", "auto") else (str(forced),)
        if any(v not in BIND_ORDER for v in order):
            raise AdapterError("Versleuteling moet 'auto', 'v1' of 'v2' zijn")
        bound = await self._bind(host, mac, order)
        if bound is None:
            self.last_diagnostics["stage"] = "bind_failed"
            raise Unsupported(
                "Binden mislukt met de oude én de nieuwe versleuteling. Dit model of deze firmware wordt "
                "(nog) niet ondersteund; de afstandsbediening blijft gewoon werken.",
                diagnostics=self.diagnostics(),
            )
        key, version = bound
        self.config["encryption"] = version  # remembered; the API stores adapter.config
        self.credentials = {"key": key}
        self.last_diagnostics.update(stage="bound", encryption=version)
        # Compatibility gate: a real status read must work before we call it paired.
        try:
            await self.read_status()
        except AdapterError as exc:
            self.last_diagnostics.update(stage="status_failed", status_error=str(exc))
            self.credentials = {}
            raise Unsupported(
                f"Gebonden, maar status lezen mislukt: {exc}. Unit blijft ‘niet ondersteund’.",
                diagnostics=self.diagnostics(),
            ) from exc
        self.last_diagnostics["stage"] = "paired"
        dev.extra.update(encryption=version, caps=self.climate_capabilities(mac), pair_diagnostics=self.diagnostics())
        return PairResult(stable_identity=mac, credentials=dict(self.credentials), device=dev)

    def instructions(self) -> PairingInstructions:
        return INSTRUCTIONS

    def _raw(self) -> dict[str, Any]:
        return (self._last_state or {}).get("raw", {})

    async def get_capabilities(self, device_identity: str) -> set[Cap]:
        caps = {Cap.POWER, Cap.HVAC_MODE, Cap.SETPOINT}
        raw = self._raw()
        if "WdSpd" in raw:
            caps.add(Cap.FAN)
        if "SwUpDn" in raw:
            caps.add(Cap.SWING)
        if (self._last_state or {}).get("indoor_c") is not None:
            caps.add(Cap.TEMPERATURE)
        return caps

    def climate_capabilities(self, device_identity: str | None = None) -> dict[str, Any]:
        """Contract C1. Fan/swing come from the last full status read (False before the first one)."""
        raw = self._raw()
        return {
            "modes": list(CAP_MODES),
            "min_c": float(MIN_C),
            "max_c": float(MAX_C),
            "step_c": 0.5 if self.half_degrees else 1.0,
            "fan": "WdSpd" in raw,
            "swing": "SwUpDn" in raw,
        }

    async def read_status(self, cols: list[str] | None = None) -> dict[str, Any]:
        full = cols is None
        cols = cols or STATUS_COLS
        now = datetime.now(UTC)
        try:
            res = await self._roundtrip({"cols": cols, "mac": self.mac, "t": "status"}, self._key(), self.version)
            if res.get("t") != "dat":
                raise AdapterError(f"Onverwacht statusantwoord: {res.get('t')}")
        except PairingRequired:
            raise
        except AdapterError as exc:
            self._failures += 1
            self._last_error, self._last_error_at = str(exc), now
            raise
        state = decode_status(
            res.get("cols", cols), res.get("dat", []), self.config.get("temsen_offset", "auto"), self.half_degrees
        )
        self._failures, self._last_error, self._last_ok_at = 0, None, now
        if full:
            self._last_state = state
        return state

    async def poll(self) -> list[Event]:
        now = datetime.now(UTC)
        state = await self.read_status()
        public = {k: v for k, v in state.items() if k != "raw"}
        public["caps"] = self.climate_capabilities(self.mac)
        events: list[Event] = [StateUpdate(self.mac, public, now)]
        if state.get("indoor_sensor"):
            q = Quality.MEASURED if state["indoor_c"] is not None else Quality.MISSING
            events.append(
                Reading(self.mac, "unit_temperature", state["indoor_c"], "°C", now, quality=q, raw=state["raw"])
            )
        return events

    async def execute(self, command: Command) -> CommandResult:
        if command.action != "set_hvac":
            raise Unsupported(f"Gree ondersteunt actie '{command.action}' niet")
        params = {k: v for k, v in command.params.items() if v is not None}
        opt, p = encode_command(params, self.half_degrees)  # validates before anything is sent
        async with self._lock:
            if self._last_state is None:
                await self.read_status()  # learn what the unit supports first
            caps = await self.get_capabilities(self.mac)
            if params.get("swing") is not None and Cap.SWING not in caps:
                raise Unsupported("Deze unit meldt geen swing-ondersteuning")
            if params.get("fan") is not None and Cap.FAN not in caps:
                raise Unsupported("Deze unit meldt geen instelbare ventilator")
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

    def _health_extra(self) -> dict[str, Any]:
        return {
            "host": self.config.get("host"),
            "mac": self.mac,
            "encryption": self.version if self.credentials.get("key") else None,
            "breaker": self._breaker.state,
            "last_ok_at": _iso(self._last_ok_at),
            "last_error": self._last_error,
            "last_error_at": _iso(self._last_error_at),
            "consecutive_failures": self._failures,
            "compatibility": Compatibility.UNTESTED.value,
        }

    async def health(self) -> Health:
        if not self.credentials.get("key"):
            return Health(HealthState.NOT_CONFIGURED, "Nog niet gekoppeld", extra=self._health_extra())
        max_age = timedelta(seconds=float(self.config.get("health_max_age_s", 90)))
        fresh = self._last_ok_at is not None and datetime.now(UTC) - self._last_ok_at <= max_age
        if not fresh:
            try:
                await self.read_status(["Pow"])
            except AdapterError as exc:
                return Health(
                    HealthState.ERROR, f"Gree-unit niet bereikbaar: {exc}", code=exc.code, extra=self._health_extra()
                )
        return Health(
            HealthState.OK,
            "Bereikbaar (compatibiliteit: nog niet getest met echte hardware)",
            extra=self._health_extra(),
        )

    def diagnostics(self) -> dict[str, Any]:
        """Support information (no keys): scan answer, bind attempts, last raw status."""
        return {
            **self.last_diagnostics,
            "host": self.config.get("host") or self.last_diagnostics.get("host"),
            "port": self.port,
            "mac": self.mac or self.last_diagnostics.get("mac"),
            "encryption": self.config.get("encryption"),
            "half_degrees": self.half_degrees,
            "breaker": self._breaker.state,
            "last_raw_status": self._raw() or None,
            "last_error": self._last_error,
            "compatibility": Compatibility.UNTESTED.value,
        }
