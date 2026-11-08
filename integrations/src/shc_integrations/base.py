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
    device_identity: str  # the physical button device (Hue: device rid, 36 chars)
    button: str  # the button resource (Hue: button / relative_rotary rid, 36 chars)
    event: str  # "initial_press", "short_release", "long_press", "rotate_clock_wise", ...
    observed_at: datetime
    # Stable id for de-duplication. Hue eventstream: "<event id>:<resource id>".
    event_id: str | None = None


@dataclass(slots=True)
class DeviceInfo:
    """Inventory/metadata of one device; carries no measurement and no online state.

    Lets the worker create a device with the right kind and name straight away, or upgrade a
    device it created earlier with a generic kind (see `should_upgrade_kind`). `removed=True`
    means the device or resource no longer exists on the hub/bridge.
    """

    device_identity: str
    kind: str  # "zigbee_sensor", "zigbee_device", "zigbee_bridge", "light", "light_group", "scene", "button", ...
    name: str | None  # None = adapter does not know a better name; keep the current one
    observed_at: datetime
    model: str | None = None
    firmware: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    removed: bool = False


Event = Reading | StateUpdate | ButtonEvent | DeviceInfo

# Kinds a device can get before the adapter knows what it is. The worker may replace these
# with the kind from a later DeviceInfo; a specific kind is never downgraded.
GENERIC_KINDS = frozenset({"device", "hue_device", "meter", "zigbee_device", "unknown"})


def should_upgrade_kind(current: str | None, new: str | None) -> bool:
    """True when a device of kind `current` should become `new` (generic -> specific only)."""
    if not new or new == current or new in GENERIC_KINDS:
        return False
    return current is None or current in GENERIC_KINDS


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
    # The device/hub received the command but reported an error for it (e.g. a Hue Bridge
    # "errors" list). Never confirmed; the API stores such a command as "failed" with `detail`.
    rejected: bool = False


@dataclass(slots=True)
class Health:
    state: HealthState
    detail: str = ""  # Dutch, plain language, shown to the user
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Machine-readable error code when not ok (same codes as AdapterError.code), e.g.
    # "device_offline", "identity_mismatch", "certificate_mismatch", "pairing_required",
    # "mqtt_disconnected", "bridge_offline", "eventstream_disconnected".
    code: str | None = None
    # Provider-specific, JSON-serialisable fields (timestamps as ISO strings). See docs per adapter.
    extra: dict[str, Any] = field(default_factory=dict)


class AdapterError(Exception):
    """Base error. `code` maps to API error codes.

    `diagnostics` is a JSON-serialisable dict the API may store (e.g. Gree scan answer when
    pairing fails); it never contains secrets.
    """

    code = "adapter_error"

    def __init__(self, message: str = "", *, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics: dict[str, Any] = dict(diagnostics or {})


class DeviceOffline(AdapterError):
    code = "device_offline"


class Unsupported(AdapterError):
    code = "unsupported"


class PairingRequired(AdapterError):
    code = "pairing_required"


class PolicyDenied(AdapterError):
    """The command is forbidden by a safety policy (e.g. protected boiler socket)."""

    code = "policy_denied"


class IdentityMismatch(AdapterError):
    """Another device answers on the configured address (e.g. after a DHCP change)."""

    code = "identity_mismatch"


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

    def device_info(self, device_identity: str) -> DeviceInfo | None:
        """What the adapter knows about a device (kind, name, model). None when unknown."""
        return None

    def inventory(self) -> list[DeviceInfo]:
        """All devices the adapter currently knows about (from the hub/bridge inventory)."""
        return []

    def climate_capabilities(self, device_identity: str) -> dict[str, Any] | None:
        """For AC units: {"modes", "min_c", "max_c", "step_c", "fan", "swing"} (contract C1); else None."""
        return None

    def diagnostics(self) -> dict[str, Any]:
        """JSON-serialisable support information without secrets."""
        return {}

    async def unpair(self, device_identity: str) -> None:  # noqa: B027
        """Forget credentials/remote state where the device supports it."""

    async def close(self) -> None:  # noqa: B027
        pass
