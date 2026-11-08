"""Zigbee via Zigbee2MQTT (MQTT). The USB coordinator is owned by the Zigbee2MQTT service.

Battery sensors report event-driven (on change / heartbeat), not every second,
so staleness thresholds must be generous and per device.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

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
    PairingInstructions,
    PairResult,
    Quality,
    Reading,
    StateUpdate,
    Unsupported,
)
from ..resilience import backoff_delays

log = logging.getLogger(__name__)

METRICS = {
    "temperature": ("temperature", "°C", (-40.0, 80.0)),
    "humidity": ("humidity", "%", (0.0, 100.0)),
    "pressure": ("pressure", "hPa", (300.0, 1100.0)),
    "battery": ("battery", "%", (0.0, 100.0)),
    "linkquality": ("link_quality", "lqi", (0.0, 255.0)),
    "voltage": ("battery_voltage", "mV", (0.0, 5000.0)),
}
CAP_FOR_PROPERTY = {
    "temperature": Cap.TEMPERATURE,
    "humidity": Cap.HUMIDITY,
    "battery": Cap.BATTERY,
    "linkquality": Cap.LINK_QUALITY,
}

INSTRUCTIONS = PairingInstructions(
    steps=[
        "Sluit de Zigbee USB-dongle met een verlengkabel aan op de server (minder storing van USB 3 en wifi).",
        "Klik op ‘Koppelen starten’: de app zet Zigbee 2 minuten open voor nieuwe apparaten.",
        "Houd de knop op de sensor ingedrukt tot het lampje knippert (bij Aqara: ca. 5 seconden).",
        "Houd de sensor tijdens het koppelen dicht bij de dongle en druk elke paar seconden kort op de knop "
        "tot de app ‘gekoppeld’ meldt.",
        "Geef de sensor een naam en ruimte en hang hem op een representatieve plek: niet in de zon, "
        "niet in de luchtstroom van de airco.",
    ],
    retry_after_s=120,
)


def parse_timestamp(value: Any) -> datetime | None:
    """Zigbee2MQTT last_seen: ISO string or epoch milliseconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def normalize_device_message(ieee: str, payload: dict[str, Any], received_at: datetime) -> list[Reading]:
    observed = parse_timestamp(payload.get("last_seen")) or received_at
    out: list[Reading] = []
    for prop, (metric, unit, (lo, hi)) in METRICS.items():
        if prop not in payload:
            continue
        val = payload[prop]
        quality = Quality.MEASURED
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not lo <= float(val) <= hi:
            val, quality = None, Quality.MISSING
        out.append(
            Reading(
                device_identity=ieee,
                metric=metric,
                value=float(val) if val is not None else None,
                unit=unit,
                observed_at=observed,
                quality=quality,
                event_id=f"zigbee:{ieee}:{metric}:{observed.isoformat()}",
                raw=payload,
            )
        )
    return out


def parse_devices(devices: list[dict[str, Any]]) -> list[DiscoveredDevice]:
    out = []
    for d in devices:
        if d.get("type") == "Coordinator":
            continue
        definition = d.get("definition") or {}
        props = sorted(_expose_properties(definition.get("exposes") or []))
        out.append(
            DiscoveredDevice(
                provider="zigbee",
                stable_identity=d["ieee_address"],
                kind="zigbee_sensor" if {"temperature", "humidity"} & set(props) else "zigbee_device",
                name=d.get("friendly_name", d["ieee_address"]),
                model=definition.get("model") or d.get("model_id"),
                firmware=d.get("software_build_id"),
                extra={
                    "vendor": definition.get("vendor"),
                    "supported": d.get("supported", False),
                    "interview_completed": d.get("interview_completed", False),
                    "power_source": d.get("power_source"),
                    "properties": props,
                },
            )
        )
    return out


def _expose_properties(exposes: list[dict[str, Any]]) -> set[str]:
    props: set[str] = set()
    for e in exposes:
        if "property" in e:
            props.add(e["property"])
        for f in e.get("features", []) or []:
            if "property" in f:
                props.add(f["property"])
    return props


def is_stale(last_seen: datetime | None, now: datetime, max_age_s: float = 3600) -> bool:
    """True when the device has not reported within `max_age_s` (or never)."""
    if last_seen is None:
        return True
    return now - last_seen > timedelta(seconds=max_age_s)


Publisher = Callable[[str, dict[str, Any]], Awaitable[None]]


class ZigbeeAdapter(Adapter):
    provider = "zigbee"

    def __init__(
        self,
        config: dict[str, Any],
        credentials: dict[str, str] | None = None,
        *,
        publisher: Publisher | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.config = dict(config)
        self.credentials = dict(credentials or {})
        self.base = self.config.get("base_topic", "zigbee2mqtt").rstrip("/")
        self._publisher = publisher
        self._sleep = sleep
        self._client = None
        self.devices: dict[str, DiscoveredDevice] = {}
        self._by_name: dict[str, str] = {}
        self.bridge_state: str | None = None
        self.bridge_seen_at: datetime | None = None
        self.connected = False
        self.last_seen: dict[str, datetime] = {}

    # -- MQTT plumbing ----------------------------------------------------
    def _client_kwargs(self) -> dict[str, Any]:
        return {
            "hostname": self.config.get("host", "mqtt"),
            "port": int(self.config.get("port", 1883)),
            "username": self.config.get("username"),
            "password": self.credentials.get("password"),
            "identifier": self.config.get("client_id", "smart-home-control"),
        }

    async def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        if self._publisher is not None:
            await self._publisher(topic, payload)
            return
        import aiomqtt

        try:
            if self._client is not None and self.connected:
                await self._client.publish(topic, json.dumps(payload), qos=1)
                return
            async with aiomqtt.Client(**self._client_kwargs()) as c:
                await c.publish(topic, json.dumps(payload), qos=1)
        except aiomqtt.MqttError as exc:
            raise DeviceOffline(f"MQTT-broker niet bereikbaar: {exc}") from exc

    def handle_message(self, topic: str, raw: bytes | str, received_at: datetime | None = None) -> list[Event]:
        """Process one MQTT message. Pure apart from updating the device cache; used by subscribe()."""
        now = received_at or datetime.now(UTC)
        if not topic.startswith(self.base + "/"):
            return []
        sub = topic[len(self.base) + 1 :]
        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            payload = json.loads(text) if text else None
        except json.JSONDecodeError:
            payload = text
        if sub == "bridge/state":
            self.bridge_state = payload.get("state") if isinstance(payload, dict) else str(payload)
            self.bridge_seen_at = now
            return [
                StateUpdate("bridge", {"bridge_state": self.bridge_state}, now, online=self.bridge_state == "online")
            ]
        if sub == "bridge/devices" and isinstance(payload, list):
            self.devices = {d.stable_identity: d for d in parse_devices(payload)}
            self._by_name = {d.name: d.stable_identity for d in self.devices.values()}
            return []
        if sub == "bridge/event" and isinstance(payload, dict):
            data = payload.get("data") or {}
            ieee = data.get("ieee_address")
            if not ieee:
                return []
            return [
                StateUpdate(
                    ieee,
                    {
                        "zigbee_event": payload.get("type"),
                        "status": data.get("status"),
                        "friendly_name": data.get("friendly_name"),
                    },
                    now,
                )
            ]
        if sub.startswith("bridge/"):
            return []
        # Device topics; ignore /set, /get, /availability sub-topics except availability.
        name, _, rest = sub.partition("/")
        ieee = self._by_name.get(name) or (name if name.startswith("0x") else None)
        if ieee is None:
            return []
        if rest == "availability":
            state = payload.get("state") if isinstance(payload, dict) else payload
            return [StateUpdate(ieee, {"availability": state}, now, online=state == "online")]
        if rest or not isinstance(payload, dict):
            return []
        readings = normalize_device_message(ieee, payload, now)
        if readings:
            self.last_seen[ieee] = max(r.observed_at for r in readings)
        return list(readings)

    # -- contract ---------------------------------------------------------
    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredDevice]:
        return list(self.devices.values())

    async def probe(self, host: str) -> DiscoveredDevice:
        raise Unsupported("Zigbee-apparaten worden gekoppeld via ‘Koppelen starten’, niet via een IP-adres")

    async def pair(self, host: str = "", **kwargs: Any) -> PairResult | PairingInstructions:
        seconds = int(kwargs.get("seconds", 120))
        await self.permit_join(seconds)
        return PairingInstructions(steps=INSTRUCTIONS.steps, retry_after_s=seconds)

    def instructions(self) -> PairingInstructions:
        return INSTRUCTIONS

    async def permit_join(self, seconds: int = 120) -> None:
        if not 0 <= seconds <= 254:
            raise AdapterError("Koppelvenster moet tussen 0 en 254 seconden liggen")
        await self._publish(f"{self.base}/bridge/request/permit_join", {"time": seconds})

    async def rename(self, ieee: str, new_name: str) -> None:
        if not new_name or "/" in new_name or "#" in new_name or "+" in new_name:
            raise AdapterError("Ongeldige naam (geen /, # of + gebruiken)")
        await self._publish(f"{self.base}/bridge/request/device/rename", {"from": ieee, "to": new_name})

    async def get_capabilities(self, device_identity: str) -> set[Cap]:
        dev = self.devices.get(device_identity)
        if dev is None:
            return set()
        return {CAP_FOR_PROPERTY[p] for p in dev.extra.get("properties", []) if p in CAP_FOR_PROPERTY}

    async def poll(self) -> list[Event]:
        return []  # event-driven via subscribe()

    async def subscribe(self) -> AsyncIterator[Event]:
        import aiomqtt

        delays = backoff_delays(1.0, 60.0)
        while True:
            try:
                async with aiomqtt.Client(**self._client_kwargs()) as client:
                    self._client = client
                    self.connected = True
                    delays = backoff_delays(1.0, 60.0)
                    await client.subscribe(f"{self.base}/#", qos=1)
                    async for msg in client.messages:
                        for ev in self.handle_message(str(msg.topic), msg.payload):
                            yield ev
            except aiomqtt.MqttError as exc:
                log.info("MQTT verbinding verbroken (%s); opnieuw verbinden", exc)
            finally:
                self.connected = False
                self._client = None
            await self._sleep(next(delays))

    async def execute(self, command: Command) -> CommandResult:
        if command.action == "permit_join":
            await self.permit_join(int(command.params.get("seconds", 120)))
            return CommandResult(ok=True, confirmed=False, detail="Koppelvenster aangevraagd")
        if command.action == "rename":
            await self.rename(command.device_identity, command.params["name"])
            return CommandResult(ok=True, confirmed=False, detail="Naamswijziging aangevraagd")
        raise Unsupported(f"Zigbee ondersteunt actie '{command.action}' niet")

    async def health(self) -> Health:
        if not self.config.get("host"):
            return Health(HealthState.NOT_CONFIGURED, "MQTT-broker niet ingesteld")
        if self.bridge_state is None:
            return Health(HealthState.WARN, "Wacht op status van Zigbee2MQTT")
        if self.bridge_state != "online":
            return Health(HealthState.ERROR, f"Zigbee2MQTT is {self.bridge_state} (USB-dongle aangesloten?)")
        stale = [
            i
            for i in self.devices
            if is_stale(self.last_seen.get(i), datetime.now(UTC), float(self.config.get("stale_after_s", 3600)))
        ]
        if stale:
            return Health(HealthState.WARN, f"{len(stale)} apparaat/apparaten zonder recente meting")
        return Health(HealthState.OK, "Zigbee2MQTT online")
