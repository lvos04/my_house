"""Contract every device adapter implements.

The API and worker only talk to adapters through this interface. Capabilities
decide which UI actions and automations are allowed; anything an adapter cannot
do is reported as unsupported, never simulated.
"""

from __future__ import annotations

import abc
import enum
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


class Quality(enum.StrEnum):
    MEASURED = "measured"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    MISSING = "missing"


class HealthState(enum.StrEnum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    NOT_CONFIGURED = "not_configured"


class Compatibility(enum.StrEnum):
    TESTED = "tested"
    UNTESTED = "untested"
    UNSUPPORTED = "unsupported"


# Capability names shared with the API/UI.
class Cap(enum.StrEnum):
    POWER = "power"  # on/off actuator
    HVAC_MODE = "hvac_mode"
    SETPOINT = "setpoint"
    FAN = "fan"
    SWING = "swing"
    BRIGHTNESS = "brightness"
    SCENE = "scene"
    BUTTON_EVENT = "button_event"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    BATTERY = "battery"
    LINK_QUALITY = "link_quality"
    MEASURED_POWER = "measured_power"
    ENERGY_IMPORT = "energy_import"
    ENERGY_EXPORT = "energy_export"
    GAS = "gas"
    RELAY = "relay"  # hardware relay present (may be forbidden by policy)


@dataclass(slots=True)
class DiscoveredDevice:
    provider: str
    stable_identity: str  # serial / MAC / bridge id / IEEE address; never an IP
    kind: str  # e.g. "hue_bridge", "p1_meter", "energy_socket", "kwh_meter", "ac_unit", "zigbee_sensor"
    name: str
    host: str | None = None
    model: str | None = None
    firmware: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PairingInstructions:
    """Returned when pairing needs a human step (press link button, enable local API...)."""

    steps: list[str]
    retry_after_s: int | None = None


@dataclass(slots=True)
class PairResult:
    stable_identity: str
    credentials: dict[str, str]  # stored encrypted by the API, never logged
    device: DiscoveredDevice


@dataclass(slots=True)
class Reading:
    """One normalized metric value."""

    device_identity: str
    metric: str  # "temperature", "humidity", "power_w", "energy_import_kwh", ...
    value: float | None
    unit: str
    observed_at: datetime
    quality: Quality = Quality.MEASURED
    event_id: str | None = None  # stable id for idempotent ingestion
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class StateUpdate:
    """Normalized actuator state (lights, AC units)."""

    device_identity: str
    state: dict[str, Any]
    observed_at: datetime
    online: bool = True


@dataclass(slots=True)
class ButtonEvent:
    device_identity: str
    button: str
    event: str  # "initial_press", "short_release", "long_press", ...
    observed_at: datetime
    event_id: str | None = None


Event = Reading | StateUpdate | ButtonEvent


@dataclass(slots=True)
class Command:
    device_identity: str
    action: str  # "set_hvac", "set_light", "set_group", "set_relay", ...
    params: dict[str, Any]


@dataclass(slots=True)
class CommandResult:
    ok: bool
    confirmed: bool  # True only after readback showed the requested state
    detail: str = ""
    state: dict[str, Any] | None = None


@dataclass(slots=True)
class Health:
    state: HealthState
    detail: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AdapterError(Exception):
    """Base error. `code` maps to API error codes."""

    code = "adapter_error"


class DeviceOffline(AdapterError):
    code = "device_offline"


class Unsupported(AdapterError):
    code = "unsupported"


class PairingRequired(AdapterError):
    code = "pairing_required"


class PolicyDenied(AdapterError):
    """The command is forbidden by a safety policy (e.g. protected boiler socket)."""

    code = "policy_denied"


class Adapter(abc.ABC):
    """One instance per configured integration (e.g. one Hue bridge)."""

    provider: str = ""

    @abc.abstractmethod
    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredDevice]: ...

    @abc.abstractmethod
    async def probe(self, host: str) -> DiscoveredDevice:
        """Identify a device by host/IP (fallback when discovery does not work)."""

    @abc.abstractmethod
    async def pair(self, host: str, **kwargs: Any) -> PairResult | PairingInstructions: ...

    @abc.abstractmethod
    def instructions(self) -> PairingInstructions:
        """Dutch step-by-step for the manufacturer side of pairing."""

    @abc.abstractmethod
    async def get_capabilities(self, device_identity: str) -> set[Cap]: ...

    @abc.abstractmethod
    async def poll(self) -> list[Event]:
        """One poll round. Adapters without polling return []."""

    async def subscribe(self) -> AsyncIterator[Event]:
        """Push events (Hue eventstream, MQTT). Default: nothing."""
        if False:  # pragma: no cover
            yield  # type: ignore[misc]

    @abc.abstractmethod
    async def execute(self, command: Command) -> CommandResult: ...

    @abc.abstractmethod
    async def health(self) -> Health: ...

    async def unpair(self, device_identity: str) -> None:  # noqa: B027
        """Forget credentials/remote state where the device supports it."""

    async def close(self) -> None:  # noqa: B027
        pass
