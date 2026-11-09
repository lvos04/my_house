"""A complete Hue CLIP v2 inventory in the documented shapes (example values, not a real bridge).

Woonkamer (room): Plafond (on) + Ledstrip (off); zone "Bank": Ledstrip; Slaapkamer: Nachtlamp (on,
but its device has connectivity issues); bridge_home owns the "all lights" group. Dimmer switch
"Dimmer gang" (4 buttons) with a Hue-app behavior on buttons 1 and 2 and a v1 rule on button 4;
Tap dial "Draaiknop" (1 button + rotary) fully handled by a behavior that names only the device.
"""

from __future__ import annotations

import copy


def rid(prefix: str, n: int) -> str:
    """36-character ids like the bridge uses."""
    return f"{prefix * 8}-0000-4000-8000-{n:012d}"


BRIDGE_HOME = rid("a", 1)
ROOM_LIVING, ROOM_BED, ZONE_SOFA = rid("b", 1), rid("b", 2), rid("b", 3)
G_ALL, G_LIVING, G_BED, G_SOFA = rid("c", 0), rid("c", 1), rid("c", 2), rid("c", 3)
D1, D2, D3, D_DIMMER, D_DIAL = rid("d", 1), rid("d", 2), rid("d", 3), rid("d", 9), rid("d", 8)
L1, L2, L3 = rid("e", 1), rid("e", 2), rid("e", 3)
Z1, Z2, Z3, Z9 = rid("f", 1), rid("f", 2), rid("f", 3), rid("f", 9)
B1, B2, B3, B4, B5, R1 = rid("1", 1), rid("1", 2), rid("1", 3), rid("1", 4), rid("1", 5), rid("2", 1)
P9 = rid("3", 9)
S_EVENING, S_BRIGHT, S_NIGHT = rid("4", 1), rid("4", 2), rid("4", 3)
BI_DIMMER, BI_DISABLED, BI_DIAL = rid("5", 1), rid("5", 2), rid("5", 3)
BRIDGE = rid("6", 1)
EVENTS = ["initial_press", "repeat", "short_release", "long_release", "long_press"]


def _device(id_, name, model, services, id_v1=None):
    d = {
        "id": id_,
        "type": "device",
        "metadata": {"name": name, "archetype": "unknown_archetype"},
        "product_data": {
            "model_id": model,
            "manufacturer_name": "Signify Netherlands B.V.",
            "product_name": name,
            "software_version": "1.122.2",
        },
        "services": [{"rid": r, "rtype": t} for r, t in services],
    }
    if id_v1:
        d["id_v1"] = id_v1
    return d


def _button(id_, owner, control_id, id_v1):
    return {
        "id": id_,
        "id_v1": id_v1,
        "type": "button",
        "owner": {"rid": owner, "rtype": "device"},
        "metadata": {"control_id": control_id},
        "button": {"event_values": EVENTS},
    }


def _light(id_, owner, name, on, bri):
    return {
        "id": id_,
        "type": "light",
        "owner": {"rid": owner, "rtype": "device"},
        "metadata": {"name": name, "archetype": "classic_bulb"},
        "on": {"on": on},
        "dimming": {"brightness": bri},
    }


def _scene(id_, name, room, active):
    return {
        "id": id_,
        "type": "scene",
        "metadata": {"name": name},
        "group": {"rid": room, "rtype": "room"},
        "status": {"active": active},
    }


_RESOURCES = [
    {"id": BRIDGE, "type": "bridge", "bridge_id": "ecb5fafffe000001", "owner": {"rid": rid("d", 0), "rtype": "device"}},
    {
        "id": BRIDGE_HOME,
        "type": "bridge_home",
        "children": [{"rid": ROOM_LIVING, "rtype": "room"}, {"rid": ROOM_BED, "rtype": "room"}],
        "services": [{"rid": G_ALL, "rtype": "grouped_light"}],
    },
    {
        "id": ROOM_LIVING,
        "type": "room",
        "metadata": {"name": "Woonkamer", "archetype": "living_room"},
        "children": [{"rid": D1, "rtype": "device"}, {"rid": D2, "rtype": "device"}],
        "services": [{"rid": G_LIVING, "rtype": "grouped_light"}],
    },
    {
        "id": ROOM_BED,
        "type": "room",
        "metadata": {"name": "Slaapkamer", "archetype": "bedroom"},
        "children": [{"rid": D3, "rtype": "device"}],
        "services": [{"rid": G_BED, "rtype": "grouped_light"}],
    },
    {
        "id": ZONE_SOFA,
        "type": "zone",
        "metadata": {"name": "Bank", "archetype": "lounge"},
        "children": [{"rid": L2, "rtype": "light"}],
        "services": [{"rid": G_SOFA, "rtype": "grouped_light"}],
    },
    {"id": G_ALL, "type": "grouped_light", "owner": {"rid": BRIDGE_HOME, "rtype": "bridge_home"}, "on": {"on": True}},
    {
        "id": G_LIVING,
        "type": "grouped_light",
        "owner": {"rid": ROOM_LIVING, "rtype": "room"},
        "on": {"on": True},
        "dimming": {"brightness": 60.0},
    },
    {"id": G_BED, "type": "grouped_light", "owner": {"rid": ROOM_BED, "rtype": "room"}, "on": {"on": True}},
    {"id": G_SOFA, "type": "grouped_light", "owner": {"rid": ZONE_SOFA, "rtype": "zone"}, "on": {"on": False}},
    _device(D1, "Plafond", "LCA001", [(L1, "light"), (Z1, "zigbee_connectivity")]),
    _device(D2, "Ledstrip", "LCL001", [(L2, "light"), (Z2, "zigbee_connectivity")]),
    _device(D3, "Nachtlamp", "LWA001", [(L3, "light"), (Z3, "zigbee_connectivity")]),
    _light(L1, D1, "Plafond", True, 60.0),
    _light(L2, D2, "Ledstrip", False, 100.0),
    _light(L3, D3, "Nachtlamp", True, 20.0),
    {"id": Z1, "type": "zigbee_connectivity", "owner": {"rid": D1, "rtype": "device"}, "status": "connected"},
    {"id": Z2, "type": "zigbee_connectivity", "owner": {"rid": D2, "rtype": "device"}, "status": "connected"},
    {"id": Z3, "type": "zigbee_connectivity", "owner": {"rid": D3, "rtype": "device"}, "status": "connectivity_issue"},
    _device(
        D_DIMMER,
        "Dimmer gang",
        "RWL022",
        [
            (B1, "button"),
            (B2, "button"),
            (B3, "button"),
            (B4, "button"),
            (P9, "device_power"),
            (Z9, "zigbee_connectivity"),
        ],
        id_v1="/sensors/5",
    ),
    _button(B1, D_DIMMER, 1, "/sensors/5"),
    _button(B2, D_DIMMER, 2, "/sensors/5"),
    _button(B3, D_DIMMER, 3, "/sensors/5"),
    _button(B4, D_DIMMER, 4, "/sensors/5"),
    {
        "id": P9,
        "type": "device_power",
        "owner": {"rid": D_DIMMER, "rtype": "device"},
        "power_state": {"battery_state": "normal", "battery_level": 87},
    },
    {"id": Z9, "type": "zigbee_connectivity", "owner": {"rid": D_DIMMER, "rtype": "device"}, "status": "connected"},
    _device(D_DIAL, "Draaiknop", "RDM002", [(B5, "button"), (R1, "relative_rotary")], id_v1="/sensors/7"),
    _button(B5, D_DIAL, 1, "/sensors/7"),
    {
        "id": R1,
        "type": "relative_rotary",
        "owner": {"rid": D_DIAL, "rtype": "device"},
        "relative_rotary": {},
    },
    _scene(S_EVENING, "Avond", ROOM_LIVING, "static"),
    _scene(S_BRIGHT, "Helder", ROOM_LIVING, "inactive"),
    _scene(S_NIGHT, "Nacht", ROOM_BED, "inactive"),
    {
        "id": BI_DIMMER,
        "type": "behavior_instance",
        "enabled": True,
        "script_id": "67d9395b-4403-42cc-b5f0-740b699d67c6",
        "metadata": {"name": "Dimmer gang"},
        "configuration": {
            "buttons": {
                B1: {"on_short_release": {"recall_single": [{"action": "all_off"}]}},
                B2: {"on_long_press": {"action": "dim_up"}},
            },
            "device": {"rid": D_DIMMER, "rtype": "device"},
            "where": [{"group": {"rid": ROOM_LIVING, "rtype": "room"}}],
        },
        "dependees": [
            {"target": {"rid": D_DIMMER, "rtype": "device"}, "level": "critical", "type": "ResourceDependee"}
        ],
    },
    {
        "id": BI_DISABLED,
        "type": "behavior_instance",
        "enabled": False,
        "metadata": {"name": "Oud"},
        "configuration": {"buttons": {B3: {"on_short_release": {}}}, "device": {"rid": D_DIMMER, "rtype": "device"}},
    },
    {
        "id": BI_DIAL,
        "type": "behavior_instance",
        "enabled": True,
        "metadata": {"name": "Draaiknop woonkamer"},
        "configuration": {"device": {"rid": D_DIAL, "rtype": "device"}},
    },
]

V1_RULES = {
    "1": {
        "name": "Dimmer gang knop 4",
        "status": "enabled",
        "conditions": [
            {"address": "/sensors/5/state/buttonevent", "operator": "eq", "value": "4002"},
            {"address": "/sensors/5/state/lastupdated", "operator": "dx"},
        ],
        "actions": [],
    },
    "2": {
        "name": "Uitgeschakeld",
        "status": "disabled",
        "conditions": [{"address": "/sensors/5/state/buttonevent", "operator": "eq", "value": "3002"}],
    },
}


def full_resources() -> list[dict]:
    return copy.deepcopy(_RESOURCES)
