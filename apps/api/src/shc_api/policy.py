"""Command policy (plan §9, §12): last line of defence before anything reaches a device.

Called by the API when a command is requested AND by the worker right before
execution, so a rule, button mapping, restore or bug can never bypass it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Actions that interrupt power to whatever is plugged in / wired.
POWER_ACTIONS = {"set_relay", "set_power", "switch", "toggle", "power_off", "power_on"}

# Roles that are measure-only, whatever the hardware can do.
DEFAULT_PROTECTED_ROLES = {"boiler_energy", "boiler"}

# Device kinds that only measure. Energy sockets have a relay but are used as meters.
MEASURE_ONLY_KINDS = {"p1_meter", "kwh_meter", "energy_socket", "zigbee_sensor"}


class PolicyDenied(Exception):
    code = "policy_denied"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class DeviceFacts:
    kind: str
    roles: frozenset[str]
    protected_roles: frozenset[str]
    safe_mode: bool = False  # after restore / startup: no device commands until confirmed


def check_command(device: DeviceFacts, action: str, params: dict) -> None:
    """Raise PolicyDenied if the command must not be sent."""
    if device.safe_mode:
        raise PolicyDenied(
            "Veilige modus actief (na herstel of herstart). Bevestig eerst de apparaatkoppelingen in "
            "Systeem › Verhuisassistent."
        )
    protected = device.roles & (device.protected_roles | DEFAULT_PROTECTED_ROLES)
    if protected:
        # Protected roles (boiler): nothing at all, not only power — reading is done by polling.
        raise PolicyDenied(
            "Dit apparaat is beveiligd als alleen-meten (boiler). Schakelen is geblokkeerd door de "
            "app; let op: de HomeWizard-app zelf kan de stekker nog wel schakelen."
        )
    if device.kind in MEASURE_ONLY_KINDS and action in POWER_ACTIONS:
        raise PolicyDenied("Energiemeters worden alleen gebruikt om te meten; schakelen is uitgeschakeld.")
    if action in POWER_ACTIONS and device.kind not in {"light", "ac_unit"}:
        raise PolicyDenied("Schakelen wordt voor dit type apparaat niet ondersteund.")
