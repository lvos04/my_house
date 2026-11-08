"""Zigbee via Zigbee2MQTT (MQTT). The USB coordinator is owned by the Zigbee2MQTT service.

Battery sensors report event-driven (on change / heartbeat), not every second,
so staleness thresholds must be generous and per device.

Pairing order seen on the bus: bridge/event device_joined -> device_interview (started,
then successful with the device definition) -> bridge/devices (full list, retained) ->
<name>/availability -> <name> (measurements). Every step that tells us more about the
device yields a `DeviceInfo`, so the worker can give a device created at "joined" its
real kind (zigbee_sensor) and friendly name as soon as they are known.
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
    DeviceInfo,
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

# Stable identity of the coordinator (USB dongle + Zigbee2MQTT); the API creates the
# "zigbee_bridge" device with this identity in POST /integrations/zigbee/setup.
COORDINATOR_IDENTITY = "zigbee2mqtt-bridge"
SENSOR_PROPERTIES = frozenset({"temperature", "humidity"})

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
    if value is None or isinstance(value, bool):
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


def expose_properties(exposes: list[dict[str, Any]] | None) -> set[str]:
    """All `property` names in a Zigbee2MQTT `exposes` tree (features can be nested)."""
    props: set[str] = set()
    for e in exposes or []:
        if not isinstance(e, dict):
            continue
        if e.get("property"):
            props.add(e["property"])
        props |= expose_properties(e.get("features"))
    return props


def kind_from_definition(definition: dict[str, Any] | None) -> str:
    """zigbee_sensor when the device exposes temperature or humidity, else zigbee_device."""
    props = expose_properties((definition or {}).get("exposes"))
    return "zigbee_sensor" if props & SENSOR_PROPERTIES else "zigbee_device"


def display_name(ieee: str, friendly_name: str | None, definition: dict[str, Any] | None) -> str:
    """The friendly_name; when it is still the IEEE address, a readable vendor/model name."""
    if friendly_name and friendly_name != ieee:
        return friendly_name
    d = definition or {}
    label = " ".join(x for x in (d.get("vendor"), d.get("model")) if x)
    return f"{label} ({ieee[-4:]})" if label else f"Zigbee-apparaat {ieee[-4:]}"


def device_from_entry(d: dict[str, Any]) -> DiscoveredDevice:
    """One entry of bridge/devices (or the data of a successful device_interview event)."""
    ieee = d["ieee_address"]
    definition = d.get("definition") or {}
    props = sorted(expose_properties(definition.get("exposes")))
    return DiscoveredDevice(
        provider="zigbee",
        stable_identity=ieee,
        kind=kind_from_definition(definition),
        name=display_name(ieee, d.get("friendly_name"), definition),
        model=definition.get("model") or d.get("model_id"),
        firmware=d.get("software_build_id"),
        extra={
            "friendly_name": d.get("friendly_name") or ieee,
            "vendor": definition.get("vendor") or d.get("manufacturer"),
            "description": definition.get("description"),
            "supported": d.get("supported", bool(definition)),
            "interview_completed": d.get("interview_completed", bool(definition)),
            "power_source": d.get("power_source"),
            "disabled": bool(d.get("disabled", False)),
            "properties": props,
        },
    )


def parse_devices(devices: list[dict[str, Any]]) -> list[DiscoveredDevice]:
    return [device_from_entry(d) for d in devices if d.get("type") != "Coordinator" and d.get("ieee_address")]


def is_stale(last_seen: datetime | None, now: datetime, max_age_s: float = 3600) -> bool:
    """True when the device has not reported within `max_age_s` (or never)."""
    if last_seen is None:
        return True
    return now - last_seen > timedelta(seconds=max_age_s)


def _bridge_state(payload: Any) -> str | None:
    """bridge/state is JSON {"state": "online"} (current) or the plain text "online" (legacy)."""
    if isinstance(payload, dict):
        payload = payload.get("state")
    if isinstance(payload, str) and payload.strip():
        return payload.strip().lower()
    return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


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
        self.bridge_info: dict[str, Any] = {}
        self.coordinator_ieee: str | None = None
        self.connected = False
        self.connected_since: datetime | None = None
        self.disconnected_since: datetime | None = None
        self.first_connected_at: datetime | None = None
        self.mqtt_error: str | None = None
        self._announced_connected: bool | None = None
        self.last_seen: dict[str, datetime] = {}
        self.availability: dict[str, str] = {}

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
            async with aiomqtt.Client(**self._client_kwargs(), timeout=float(self.config.get("timeout_s", 5))) as c:
                await c.publish(topic, json.dumps(payload), qos=1)
        except aiomqtt.MqttError as exc:
            self.mqtt_error = str(exc)
            raise DeviceOffline(f"MQTT-broker niet bereikbaar: {exc}") from exc

    # -- name/identity bookkeeping ----------------------------------------
    def _remember(self, dev: DiscoveredDevice) -> None:
        self.devices[dev.stable_identity] = dev
        self._by_name = {d.extra.get("friendly_name") or d.stable_identity: i for i, d in self.devices.items()}

    def _resolve_topic(self, sub: str) -> tuple[str | None, str]:
        """(ieee, rest) for a device topic; friendly names may contain '/'."""
        best: str | None = None
        for name in self._by_name:
            if (sub == name or sub.startswith(name + "/")) and (best is None or len(name) > len(best)):
                best = name
        if best is not None:
            return self._by_name[best], sub[len(best) + 1 :]
        first, _, rest = sub.partition("/")
        return (first if first.startswith("0x") else None), rest

    def _info(self, dev: DiscoveredDevice, at: datetime, removed: bool = False) -> DeviceInfo:
        return DeviceInfo(
            device_identity=dev.stable_identity,
            kind=dev.kind,
            name=dev.name,
            observed_at=at,
            model=dev.model,
            firmware=dev.firmware,
            extra=dict(dev.extra),
            removed=removed,
        )

    def _coordinator_info(self, at: datetime) -> DeviceInfo:
        coord = self.bridge_info.get("coordinator") or {}
        meta = coord.get("meta") or {}
        firmware = meta.get("revision") or meta.get("firmware")
        return DeviceInfo(
            device_identity=COORDINATOR_IDENTITY,
            kind="zigbee_bridge",
            name=None,  # keep the name the user/API gave it
            observed_at=at,
            model=coord.get("type"),
            firmware=str(firmware) if firmware is not None else None,
            extra={
                "coordinator_ieee": self.coordinator_ieee or coord.get("ieee_address"),
                "zigbee2mqtt_version": self.bridge_info.get("version"),
                "permit_join": bool(self.bridge_info.get("permit_join", False)),
                "permit_join_end": self.bridge_info.get("permit_join_end")
                or self.bridge_info.get("permit_join_timeout"),
            },
        )

    def device_info(self, device_identity: str) -> DeviceInfo | None:
        now = datetime.now(UTC)
        if device_identity == COORDINATOR_IDENTITY:
            return self._coordinator_info(now)
        dev = self.devices.get(device_identity)
        return self._info(dev, now) if dev else None

    def inventory(self) -> list[DeviceInfo]:
        now = datetime.now(UTC)
        return [self._coordinator_info(now), *(self._info(d, now) for d in self.devices.values())]

    # -- message handling ---------------------------------------------------
    def handle_message(self, topic: str, raw: bytes | str, received_at: datetime | None = None) -> list[Event]:
        """Process one MQTT message. Pure apart from updating the device cache; used by subscribe()."""
        now = received_at or datetime.now(UTC)
        if not topic.startswith(self.base + "/"):
            return []
        sub = topic[len(self.base) + 1 :]
        text = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
        try:
            payload = json.loads(text) if text else None
        except json.JSONDecodeError:
            payload = text
        if sub == "bridge/state":
            state = _bridge_state(payload)
            if state is None:
                return []
            self.bridge_state, self.bridge_seen_at = state, now
            return [
                StateUpdate(
                    COORDINATOR_IDENTITY,
                    {"bridge_state": state, "mqtt_connected": True},
                    now,
                    online=state == "online",
                )
            ]
        if sub == "bridge/info" and isinstance(payload, dict):
            self.bridge_info = payload
            coord = payload.get("coordinator") or {}
            self.coordinator_ieee = coord.get("ieee_address") or self.coordinator_ieee
            return [self._coordinator_info(now)]
        if sub == "bridge/devices" and isinstance(payload, list):
            return self._handle_device_list(payload, now)
        if sub == "bridge/event" and isinstance(payload, dict):
            return self._handle_bridge_event(payload, now)
        if sub.startswith("bridge/"):
            return []
        ieee, rest = self._resolve_topic(sub)
        if ieee is None:
            return []
        if rest == "availability":
            state = _bridge_state(payload)
            if state is None:
                return []
            self.availability[ieee] = state
            return [StateUpdate(ieee, {"availability": state}, now, online=state == "online")]
        if rest or not isinstance(payload, dict):
            return []  # /set, /get and other sub-topics
        readings = normalize_device_message(ieee, payload, now)
        if readings:
            self.last_seen[ieee] = max(r.observed_at for r in readings)
        return list(readings)

    def _handle_device_list(self, payload: list[dict[str, Any]], now: datetime) -> list[Event]:
        for d in payload:
            if d.get("type") == "Coordinator" and d.get("ieee_address"):
                self.coordinator_ieee = d["ieee_address"]
        new = {d.stable_identity: d for d in parse_devices(payload)}
        gone = [dev for ieee, dev in self.devices.items() if ieee not in new]
        self.devices = {}
        for dev in new.values():
            self._remember(dev)
        if not self.devices:
            self._by_name = {}
        out: list[Event] = [self._coordinator_info(now)]
        out += [self._info(dev, now) for dev in new.values()]
        out += [self._info(dev, now, removed=True) for dev in gone]
        return out

    def _handle_bridge_event(self, payload: dict[str, Any], now: datetime) -> list[Event]:
        etype = payload.get("type")
        data = payload.get("data") or {}
        ieee = data.get("ieee_address")
        if not ieee:
            return []
        friendly = data.get("friendly_name") or ieee
        status = data.get("status")
        out: list[Event] = []
        if etype == "device_leave":
            dev = self.devices.pop(ieee, None)
            self._by_name = {n: i for n, i in self._by_name.items() if i != ieee}
            if dev is not None:
                out.append(self._info(dev, now, removed=True))
            out.append(StateUpdate(ieee, {"zigbee_event": etype, "status": status}, now, online=False))
            return out
        if etype == "device_interview" and status == "successful" and data.get("definition"):
            dev = device_from_entry({**data, "interview_completed": True, "supported": data.get("supported", True)})
            self._remember(dev)
            out.append(self._info(dev, now))
        elif ieee not in self.devices:
            # Joined/announced/interviewing: we know the address and the name, not yet the type.
            placeholder = device_from_entry({"ieee_address": ieee, "friendly_name": friendly})
            placeholder.extra["interview_completed"] = False
            self._remember(placeholder)
            out.append(self._info(placeholder, now))
        out.append(
            StateUpdate(
                ieee,
                {"zigbee_event": etype, "status": status, "friendly_name": friendly},
                now,
                online=not (etype == "device_interview" and status == "failed"),
            )
        )
        return out

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

    def _connection_event(self, now: datetime) -> StateUpdate:
        return StateUpdate(
            COORDINATOR_IDENTITY,
            {"mqtt_connected": self.connected, "bridge_state": self.bridge_state, "mqtt_error": self.mqtt_error},
            now,
            online=self.connected and self.bridge_state == "online",
        )

    async def subscribe(self) -> AsyncIterator[Event]:
        """Follow all Zigbee2MQTT topics forever. MQTT outages are reported, never raised."""
        import aiomqtt

        delays = backoff_delays(1.0, 60.0)
        while True:
            try:
                async with aiomqtt.Client(**self._client_kwargs()) as client:
                    self._client = client
                    self.connected, self.mqtt_error = True, None
                    self.connected_since = datetime.now(UTC)
                    self.first_connected_at = self.first_connected_at or self.connected_since
                    self.disconnected_since = None
                    delays = backoff_delays(1.0, 60.0)
                    if self._announced_connected is not True:
                        log.info("MQTT verbonden met %s", self._client_kwargs()["hostname"])
                        self._announced_connected = True
                    await client.subscribe(f"{self.base}/#", qos=1)
                    async for msg in client.messages:
                        for ev in self.handle_message(str(msg.topic), msg.payload):
                            yield ev
            except aiomqtt.MqttError as exc:
                self.mqtt_error = str(exc)
            finally:
                self.connected = False
                self._client = None
            now = datetime.now(UTC)
            self.disconnected_since = self.disconnected_since or now
            if self._announced_connected is not False:
                # One log line and one event per status change, not per reconnect attempt.
                log.info("MQTT niet verbonden (%s); opnieuw verbinden met backoff", self.mqtt_error)
                self._announced_connected = False
                yield self._connection_event(now)
            await self._sleep(next(delays))

    async def execute(self, command: Command) -> CommandResult:
        if command.action == "permit_join":
            await self.permit_join(int(command.params.get("seconds", 120)))
            return CommandResult(ok=True, confirmed=False, detail="Koppelvenster aangevraagd")
        if command.action == "rename":
            await self.rename(command.device_identity, command.params["name"])
            return CommandResult(ok=True, confirmed=False, detail="Naamswijziging aangevraagd")
        raise Unsupported(f"Zigbee ondersteunt actie '{command.action}' niet")

    def _stale_devices(self, now: datetime) -> list[str]:
        max_age = float(self.config.get("stale_after_s", 3600))
        # After a (re)start we only know what arrived since then: give sensors max_age to report.
        since = self.first_connected_at
        if since is None or now - since <= timedelta(seconds=max_age):
            return []
        return [
            ieee
            for ieee, dev in self.devices.items()
            if dev.kind == "zigbee_sensor"
            and not dev.extra.get("disabled")
            and is_stale(self.last_seen.get(ieee), now, max_age)
        ]

    def _health_extra(self, now: datetime) -> dict[str, Any]:
        return {
            "mqtt_connected": self.connected,
            "mqtt_connected_since": _iso(self.connected_since),
            "mqtt_disconnected_since": _iso(self.disconnected_since),
            "mqtt_error": self.mqtt_error,
            "bridge_state": self.bridge_state,
            "bridge_online": None if self.bridge_state is None else self.bridge_state == "online",
            "bridge_seen_at": _iso(self.bridge_seen_at),
            "zigbee2mqtt_version": self.bridge_info.get("version"),
            "coordinator_type": (self.bridge_info.get("coordinator") or {}).get("type"),
            "devices": len(self.devices),
            "stale_devices": self._stale_devices(now),
        }

    async def health(self) -> Health:
        now = datetime.now(UTC)
        extra = self._health_extra(now)
        if not self.config.get("host"):
            return Health(HealthState.NOT_CONFIGURED, "MQTT-broker niet ingesteld", extra=extra)
        if not self.connected:
            if self.mqtt_error is None and self.disconnected_since is None:
                return Health(
                    HealthState.WARN, "Nog niet verbonden met de MQTT-broker", code="mqtt_connecting", extra=extra
                )
            return Health(
                HealthState.ERROR,
                "Geen verbinding met de MQTT-broker (mosquitto). Zigbee-metingen komen niet binnen.",
                code="mqtt_disconnected",
                extra=extra,
            )
        if self.bridge_state is None:
            return Health(
                HealthState.WARN,
                "Verbonden met MQTT, maar Zigbee2MQTT meldt zich niet (draait de Zigbee2MQTT-container?)",
                code="bridge_unknown",
                extra=extra,
            )
        if self.bridge_state != "online":
            return Health(
                HealthState.ERROR,
                f"Zigbee2MQTT is {self.bridge_state}: controleer of de USB-dongle is aangesloten.",
                code="bridge_offline",
                extra=extra,
            )
        if extra["stale_devices"]:
            n = len(extra["stale_devices"])
            return Health(HealthState.WARN, f"{n} sensor(en) zonder recente meting", code="stale_devices", extra=extra)
        return Health(HealthState.OK, "MQTT verbonden, Zigbee2MQTT online", extra=extra)

    def diagnostics(self) -> dict[str, Any]:
        return {"base_topic": self.base, **self._health_extra(datetime.now(UTC))}
