"""Cached Hue CLIP v2 resources and everything derived from them.

Pure (no I/O): the adapter feeds it the full resource list (GET /clip/v2/resource) and
every eventstream container; the model merges partial updates into its cache and
returns normalized events with the derived state the app needs:

- lights: on/brightness plus reachability from the owner device's zigbee_connectivity;
- light groups (grouped_light): name of the room/zone ("Alle lampen" for the bridge-wide
  group), lights on / total / reachable, and the active scene from scene.status.active;
- button devices: name, model, buttons with their possible events, and which buttons the
  bridge itself already handles ("bridge_rules": from behavior_instance resources and,
  when available, the bridge's v1 rules).
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from ..base import ButtonEvent, DeviceInfo, Event, StateUpdate

RESOURCE_TYPES = (
    "light",
    "grouped_light",
    "room",
    "zone",
    "bridge_home",
    "scene",
    "device",
    "device_power",
    "button",
    "relative_rotary",
    "zigbee_connectivity",
    "behavior_instance",
    "bridge",
)
ALL_LIGHTS_NAME = "Alle lampen"
HUE_ID_RE = re.compile(r"^[0-9a-z]{8}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{12}$", re.IGNORECASE)
DEFAULT_BUTTON_EVENTS = ["initial_press", "repeat", "short_release", "long_press", "long_release"]
ROTARY_EVENTS = ["rotate_clock_wise", "rotate_counter_clock_wise"]
BUTTON_TYPES = ("button", "relative_rotary")
_V1_SENSOR_ADDR = re.compile(r"^/sensors/(\d+)/state/(buttonevent|rotaryevent|expectedrotation)$")


def is_hue_id(value: Any) -> bool:
    """CLIP v2 resource ids are 36-character UUID strings."""
    return isinstance(value, str) and bool(HUE_ID_RE.match(value))


def parse_ts(value: str | None, default: datetime | None = None) -> datetime:
    if value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return default or datetime.now(UTC)


def deep_merge(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in delta.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def light_basic_state(res: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {"type": res.get("type")}
    if "on" in res:
        state["on"] = bool((res.get("on") or {}).get("on"))
    if res.get("dimming") is not None:
        state["brightness"] = res["dimming"].get("brightness")
    if "owner" in res:
        state["owner"] = (res.get("owner") or {}).get("rid")
    if "name" in (res.get("metadata") or {}):
        state["name"] = res["metadata"]["name"]
    return state


def _collect_refs(obj: Any) -> set[str]:
    """Every resource id referenced in a behavior_instance (values of "rid" and id-shaped keys)."""
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "rid" and isinstance(v, str):
                out.add(v)
            elif is_hue_id(k):
                out.add(k)
            out |= _collect_refs(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _collect_refs(v)
    return out


class HueModel:
    def __init__(self, resources: dict[str, dict[str, Any]] | None = None):
        self.resources: dict[str, dict[str, Any]] = resources if resources is not None else {}
        self.v1_rules: dict[str, set[str]] = {}  # device rid -> button rids used in v1 rules
        self.v1_rule_names: dict[str, set[str]] = {}

    # -- loading ---------------------------------------------------------
    def load(self, items: list[dict[str, Any]]) -> None:
        """Replace the cache (the dict object is kept: the worker may hold a reference)."""
        self.resources.clear()
        for r in items:
            if r.get("type") in RESOURCE_TYPES and r.get("id"):
                self.resources[r["id"]] = r

    def of_type(self, rtype: str) -> list[dict[str, Any]]:
        return [r for r in self.resources.values() if r.get("type") == rtype]

    def get(self, rid: str | None) -> dict[str, Any] | None:
        return self.resources.get(rid) if rid else None

    # -- relations ---------------------------------------------------------
    @staticmethod
    def owner_of(res: dict[str, Any] | None) -> str | None:
        return ((res or {}).get("owner") or {}).get("rid")

    def name_of(self, rid: str | None) -> str | None:
        return ((self.get(rid) or {}).get("metadata") or {}).get("name")

    def device_services(self, device_rid: str | None, rtype: str) -> list[str]:
        dev = self.get(device_rid) or {}
        rids = [s.get("rid") for s in dev.get("services") or [] if s.get("rtype") == rtype and s.get("rid")]
        if not rids:  # fall back to scanning owners (partial resource lists)
            rids = [r["id"] for r in self.of_type(rtype) if self.owner_of(r) == device_rid]
        return rids

    def connectivity(self, device_rid: str | None) -> str | None:
        for rid in self.device_services(device_rid, "zigbee_connectivity"):
            status = (self.get(rid) or {}).get("status")
            if status:
                return status
        return None

    def light_reachable(self, light_rid: str) -> bool | None:
        status = self.connectivity(self.owner_of(self.get(light_rid)))
        return None if status is None else status == "connected"

    def lights_of_owner(self, owner_rid: str | None) -> list[str]:
        owner = self.get(owner_rid) or {}
        if owner.get("type") == "bridge_home":
            return sorted(r["id"] for r in self.of_type("light"))
        out: list[str] = []
        for child in owner.get("children") or []:
            if child.get("rtype") == "light":
                out.append(child["rid"])
            elif child.get("rtype") == "device":
                out += self.device_services(child.get("rid"), "light")
        return [rid for rid in dict.fromkeys(out) if rid in self.resources]

    def grouped_light_of_owner(self, owner_rid: str | None) -> str | None:
        for g in self.of_type("grouped_light"):
            if self.owner_of(g) == owner_rid:
                return g["id"]
        return None

    def groups_containing_light(self, light_rid: str) -> list[str]:
        return [g["id"] for g in self.of_type("grouped_light") if light_rid in self.lights_of_owner(self.owner_of(g))]

    def active_scene(self, owner_rid: str | None) -> dict[str, Any] | None:
        active = [
            s
            for s in self.of_type("scene")
            if ((s.get("group") or {}).get("rid") == owner_rid)
            and ((s.get("status") or {}).get("active") or "inactive") != "inactive"
        ]
        if not active:
            return None
        return max(active, key=lambda s: str((s.get("status") or {}).get("last_recall") or ""))

    def button_resources(self, device_rid: str) -> list[dict[str, Any]]:
        rids = self.device_services(device_rid, "button") + self.device_services(device_rid, "relative_rotary")
        return [self.resources[r] for r in rids if r in self.resources]

    def button_devices(self) -> list[str]:
        devs = {self.owner_of(r) for t in BUTTON_TYPES for r in self.of_type(t)}
        return sorted(d for d in devs if d)

    def buttons_of_device(self, device_rid: str) -> list[dict[str, Any]]:
        out = []
        for res in self.button_resources(device_rid):
            if res.get("type") == "button":
                events = list((res.get("button") or {}).get("event_values") or DEFAULT_BUTTON_EVENTS)
            else:
                events = list(ROTARY_EVENTS)
            out.append(
                {
                    "id": res["id"],
                    "control_id": (res.get("metadata") or {}).get("control_id"),
                    "type": res.get("type"),
                    "events": events,
                }
            )
        return sorted(out, key=lambda b: (b["type"] != "button", b["control_id"] or 0, b["id"]))

    # -- bridge rules ------------------------------------------------------
    def _button_owner_map(self) -> tuple[dict[str, str], dict[str, list[str]]]:
        owner: dict[str, str] = {}
        by_device: dict[str, list[str]] = defaultdict(list)
        for t in BUTTON_TYPES:
            for r in self.of_type(t):
                dev = self.owner_of(r)
                if dev:
                    owner[r["id"]] = dev
                    by_device[dev].append(r["id"])
        return owner, by_device

    def bridge_rules(self) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """(device rid -> button rids the bridge handles itself, device rid -> rule names)."""
        owner, by_device = self._button_owner_map()
        rules: dict[str, set[str]] = defaultdict(set)
        names: dict[str, set[str]] = defaultdict(set)
        for bi in self.of_type("behavior_instance"):
            if bi.get("enabled") is False:
                continue
            refs = _collect_refs(bi.get("configuration")) | _collect_refs(bi.get("dependees"))
            specific = {r for r in refs if r in owner}
            touched = {owner[r] for r in specific}
            for r in specific:
                rules[owner[r]].add(r)
            for r in refs:  # the whole device referenced without naming buttons: all its buttons
                if r in by_device and r not in touched:
                    rules[r].update(by_device[r])
                    touched.add(r)
            label = (bi.get("metadata") or {}).get("name") or "Hue-automatisering"
            for dev in touched:
                names[dev].add(label)
        for dev, rids in self.v1_rules.items():
            rules[dev] |= rids
            names[dev] |= self.v1_rule_names.get(dev, set())
        return rules, names

    def set_v1_rules(self, rules: dict[str, Any]) -> None:
        """Map the bridge's v1 rules (GET /api/<key>/rules) onto v2 button rids via id_v1."""
        by_sensor: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in BUTTON_TYPES:
            for r in self.of_type(t):
                id_v1 = r.get("id_v1") or (self.get(self.owner_of(r)) or {}).get("id_v1")
                if id_v1:
                    by_sensor[id_v1].append(r)
        found: dict[str, set[str]] = defaultdict(set)
        names: dict[str, set[str]] = defaultdict(set)
        for rule in (rules or {}).values():
            if not isinstance(rule, dict) or rule.get("status", "enabled") != "enabled":
                continue
            for cond in rule.get("conditions") or []:
                m = _V1_SENSOR_ADDR.match(str(cond.get("address", "")))
                if not m:
                    continue
                candidates = by_sensor.get(f"/sensors/{m.group(1)}", [])
                if (
                    m.group(2) == "buttonevent"
                    and cond.get("operator") == "eq"
                    and str(cond.get("value", "")).isdigit()
                ):
                    control = int(cond["value"]) // 1000
                    exact = [r for r in candidates if (r.get("metadata") or {}).get("control_id") == control]
                    candidates = exact or candidates
                for r in candidates:
                    dev = self.owner_of(r)
                    if dev:
                        found[dev].add(r["id"])
                        names[dev].add(str(rule.get("name") or "Hue-regel"))
        self.v1_rules, self.v1_rule_names = dict(found), dict(names)

    # -- derived state -----------------------------------------------------
    def light_state(self, rid: str) -> dict[str, Any]:
        res = self.get(rid) or {}
        state = light_basic_state(res)
        state["type"] = "light"
        state["reachable"] = self.light_reachable(rid)
        return state

    def group_state(self, rid: str) -> dict[str, Any]:
        res = self.get(rid) or {}
        owner_rid = self.owner_of(res)
        owner = self.get(owner_rid) or {}
        owner_type = owner.get("type") or ((res.get("owner") or {}).get("rtype"))
        lights = self.lights_of_owner(owner_rid)
        reach = [self.light_reachable(lr) for lr in lights]
        scene = self.active_scene(owner_rid)
        state = light_basic_state(res)
        state.update(
            type="grouped_light",
            owner=owner_rid,
            owner_type=owner_type,
            all_lights=owner_type == "bridge_home",
            name=ALL_LIGHTS_NAME if owner_type == "bridge_home" else self.name_of(owner_rid),
            lights_on=sum(1 for lr in lights if ((self.get(lr) or {}).get("on") or {}).get("on")),
            lights_total=len(lights),
            lights_reachable=sum(1 for r in reach if r is not False),
            scene=self.name_of(scene["id"]) if scene else None,
            scene_id=scene["id"] if scene else None,
        )
        return state

    def button_device_state(self, device_rid: str) -> dict[str, Any]:
        dev = self.get(device_rid) or {}
        product = dev.get("product_data") or {}
        rules, names = self.bridge_rules()
        battery = None
        for rid in self.device_services(device_rid, "device_power"):
            battery = ((self.get(rid) or {}).get("power_state") or {}).get("battery_level", battery)
        return {
            "type": "button_device",
            "name": self.name_of(device_rid),
            "model": product.get("model_id"),
            "product_name": product.get("product_name"),
            "buttons": self.buttons_of_device(device_rid),
            "bridge_rules": sorted(rules.get(device_rid, set())),
            "bridge_rule_names": sorted(names.get(device_rid, set())),
            "connectivity": self.connectivity(device_rid),
            "battery": battery,
        }

    # -- events ------------------------------------------------------------
    def light_update(self, rid: str, at: datetime) -> StateUpdate:
        st = self.light_state(rid)
        return StateUpdate(rid, st, at, online=st["reachable"] is not False)

    def group_update(self, rid: str, at: datetime) -> StateUpdate:
        st = self.group_state(rid)
        return StateUpdate(rid, st, at, online=st["lights_total"] == 0 or st["lights_reachable"] > 0)

    def button_device_update(self, device_rid: str, at: datetime) -> StateUpdate:
        st = self.button_device_state(device_rid)
        return StateUpdate(device_rid, st, at, online=st["connectivity"] in (None, "connected"))

    def _device_meta(self, device_rid: str | None) -> tuple[str | None, str | None, dict[str, Any]]:
        dev = self.get(device_rid) or {}
        product = dev.get("product_data") or {}
        return (
            product.get("model_id"),
            product.get("software_version"),
            {"manufacturer": product.get("manufacturer_name"), "product_name": product.get("product_name")},
        )

    def info_for(self, rid: str, at: datetime) -> DeviceInfo | None:
        res = self.get(rid)
        if res is None:
            return None
        rtype = res.get("type")
        if rtype == "light":
            model, fw, extra = self._device_meta(self.owner_of(res))
            extra.update(device=self.owner_of(res), archetype=(res.get("metadata") or {}).get("archetype"))
            return DeviceInfo(rid, "light", self.name_of(rid), at, model=model, firmware=fw, extra=extra)
        if rtype == "grouped_light":
            st = self.group_state(rid)
            extra = {"owner": st["owner"], "owner_type": st["owner_type"], "all_lights": st["all_lights"]}
            return DeviceInfo(rid, "light_group", st["name"], at, extra=extra)
        if rtype == "scene":
            group_owner = (res.get("group") or {}).get("rid")
            extra = {
                "group": group_owner,
                "group_type": (res.get("group") or {}).get("rtype"),
                "grouped_light": self.grouped_light_of_owner(group_owner),
            }
            return DeviceInfo(rid, "scene", self.name_of(rid), at, extra=extra)
        if rtype == "device" and rid in self.button_devices():
            model, fw, extra = self._device_meta(rid)
            st = self.button_device_state(rid)
            extra.update(buttons=st["buttons"], bridge_rules=st["bridge_rules"])
            return DeviceInfo(rid, "button", st["name"], at, model=model, firmware=fw, extra=extra)
        return None

    def inventory(self, at: datetime) -> list[DeviceInfo]:
        rids = [r["id"] for t in ("light", "grouped_light", "scene") for r in self.of_type(t)]
        rids += self.button_devices()
        return [i for i in (self.info_for(rid, at) for rid in rids) if i is not None]

    def snapshot(self, at: datetime) -> list[Event]:
        """Everything after a full load: inventory first, then the current state of each entity."""
        events: list[Event] = list(self.inventory(at))
        events += [self.light_update(r["id"], at) for r in self.of_type("light")]
        events += [self.group_update(r["id"], at) for r in self.of_type("grouped_light")]
        events += [self.button_device_update(d, at) for d in self.button_devices()]
        return events

    def _button_event(self, res: dict[str, Any], at: datetime, event_id_prefix: str | None) -> ButtonEvent | None:
        rid = res["id"]
        device = self.owner_of(res) or self.owner_of(self.get(rid)) or rid
        updated: str | None = None
        if res.get("type") == "button":
            btn = res.get("button") or {}
            report = btn.get("button_report")
            if report:
                event, updated = report.get("event", "unknown"), report.get("updated")
            elif btn.get("last_event"):
                event = btn["last_event"]
            else:
                return None
        else:
            rr = res.get("relative_rotary") or {}
            report = rr.get("rotary_report") or rr.get("last_event")
            if not report:
                return None
            event = f"rotate_{(report.get('rotation') or {}).get('direction', 'unknown')}"
            updated = report.get("updated")
        if event_id_prefix:
            event_id = f"{event_id_prefix}:{rid}"
        else:
            event_id = f"{rid}:{updated}" if updated else None
        return ButtonEvent(device, rid, event, parse_ts(updated, at), event_id=event_id)

    def apply_container(self, container: dict[str, Any], sse_id: str | None = None) -> list[Event]:
        """Merge one eventstream container into the cache and return the resulting events."""
        ctype = container.get("type", "update")
        at = parse_ts(container.get("creationtime"))
        prefix = container.get("id") or sse_id
        buttons: list[Event] = []
        infos: list[DeviceInfo] = []
        lights: set[str] = set()
        groups: set[str] = set()
        button_devs: set[str] = set()
        info_rids: set[str] = set()
        all_groups = False
        for res in container.get("data") or []:
            rid, rtype = res.get("id"), res.get("type")
            if not rid or not rtype:
                continue
            if ctype == "delete":
                old = self.resources.pop(rid, None) or res
                kind = {"light": "light", "grouped_light": "light_group", "scene": "scene"}.get(rtype)
                if rtype == "device" and any(self.owner_of(r) == rid for t in BUTTON_TYPES for r in self.of_type(t)):
                    kind = "button"
                if kind:
                    infos.append(
                        DeviceInfo(
                            rid, kind, self.name_of(rid) or (old.get("metadata") or {}).get("name"), at, removed=True
                        )
                    )
                all_groups = True
                continue
            if rtype in RESOURCE_TYPES:
                if ctype == "add" or rid not in self.resources:
                    self.resources[rid] = copy.deepcopy(res)
                else:
                    self.resources[rid] = deep_merge(self.resources[rid], res)
            renamed = ctype == "add" or "metadata" in res
            if rtype in BUTTON_TYPES:
                ev = self._button_event(res, at, prefix)
                if ev is not None:
                    buttons.append(ev)
                if renamed:
                    button_devs.add(self.owner_of(self.get(rid)) or "")
            elif rtype == "light":
                lights.add(rid)
                if renamed:
                    info_rids.add(rid)
            elif rtype == "grouped_light":
                groups.add(rid)
                if ctype == "add":
                    info_rids.add(rid)
            elif rtype in ("zigbee_connectivity", "device_power"):
                dev = self.owner_of(self.get(rid))
                if rtype == "zigbee_connectivity":
                    lights.update(self.device_services(dev, "light"))
                if dev in self.button_devices():
                    button_devs.add(dev)
            elif rtype == "scene":
                g = self.grouped_light_of_owner(((self.get(rid) or {}).get("group") or {}).get("rid"))
                if g:
                    groups.add(g)
                if renamed:
                    info_rids.add(rid)
            elif rtype in ("room", "zone", "bridge_home"):
                g = self.grouped_light_of_owner(rid)
                if g:
                    groups.add(g)
                    if renamed:
                        info_rids.add(g)
                all_groups = all_groups or "children" in res
            elif rtype == "device":
                lights.update(self.device_services(rid, "light"))
                if rid in self.button_devices():
                    button_devs.add(rid)
                if renamed:
                    info_rids.update(self.device_services(rid, "light"))
                    if rid in self.button_devices():
                        info_rids.add(rid)
            elif rtype == "behavior_instance":
                button_devs.update(self.button_devices())
        lights = {lr for lr in lights if lr in self.resources}
        for lr in lights:
            groups.update(self.groups_containing_light(lr))
        if all_groups:
            groups.update(g["id"] for g in self.of_type("grouped_light"))
        button_devs.discard("")
        infos += [i for i in (self.info_for(rid, at) for rid in sorted(info_rids)) if i is not None]
        states: list[Event] = [self.light_update(lr, at) for lr in sorted(lights)]
        states += [self.group_update(g, at) for g in sorted(groups) if g in self.resources]
        states += [self.button_device_update(d, at) for d in sorted(button_devs) if d in self.resources]
        return [*buttons, *infos, *states]
