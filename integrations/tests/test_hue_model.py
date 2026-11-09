"""Items 25 and 27 (Hue): inventory, bridge rules, group counts, scenes, reachability, eventstream merging."""

from datetime import UTC, datetime

from hue_inventory import (
    B1,
    B2,
    B3,
    B4,
    B5,
    D1,
    D_DIAL,
    D_DIMMER,
    G_ALL,
    G_BED,
    G_LIVING,
    G_SOFA,
    L1,
    L2,
    L3,
    R1,
    S_BRIGHT,
    S_EVENING,
    V1_RULES,
    Z1,
    full_resources,
)

from shc_integrations.base import ButtonEvent, DeviceInfo, StateUpdate
from shc_integrations.hue import ALL_LIGHTS_NAME, HueModel, is_hue_id

AT = datetime(2026, 11, 8, 13, 40, tzinfo=UTC)


def model(v1=True) -> HueModel:
    m = HueModel()
    m.load(full_resources())
    if v1:
        m.set_v1_rules(V1_RULES)
    return m


def by_id(events, cls=StateUpdate):
    return {e.device_identity: e for e in events if isinstance(e, cls)}


def test_group_counts_scene_and_all_lights():
    st = by_id(model().snapshot(AT))
    living = st[G_LIVING].state
    assert living["name"] == "Woonkamer" and living["owner_type"] == "room"
    assert (living["lights_on"], living["lights_total"], living["lights_reachable"]) == (1, 2, 2)
    assert living["scene"] == "Avond" and living["scene_id"] == S_EVENING
    sofa = st[G_SOFA].state
    assert sofa["name"] == "Bank" and (sofa["lights_on"], sofa["lights_total"]) == (0, 1)  # zone: lights as children
    bed = st[G_BED]
    assert (bed.state["lights_on"], bed.state["lights_total"], bed.state["lights_reachable"]) == (1, 1, 0)
    assert bed.online is False and bed.state["scene"] is None
    everything = st[G_ALL].state
    assert everything["name"] == ALL_LIGHTS_NAME and everything["all_lights"] is True
    assert (everything["lights_on"], everything["lights_total"]) == (2, 3)


def test_light_reachability_via_owner_device():
    st = by_id(model().snapshot(AT))
    assert st[L1].state["reachable"] is True and st[L1].online is True
    assert st[L3].state["reachable"] is False and st[L3].online is False
    assert st[L1].state["name"] == "Plafond" and st[L1].state["type"] == "light"


def test_button_inventory_and_bridge_rules():
    m = model()
    st = by_id(m.snapshot(AT))
    dimmer = st[D_DIMMER].state
    assert dimmer["type"] == "button_device" and dimmer["name"] == "Dimmer gang" and dimmer["model"] == "RWL022"
    assert [b["id"] for b in dimmer["buttons"]] == [B1, B2, B3, B4]
    assert dimmer["buttons"][0]["control_id"] == 1 and "long_press" in dimmer["buttons"][0]["events"]
    # behavior on buttons 1+2, v1 rule on button 4; the disabled behavior (button 3) does not count.
    assert dimmer["bridge_rules"] == sorted([B1, B2, B4])
    assert dimmer["bridge_rule_names"] == ["Dimmer gang", "Dimmer gang knop 4"]
    assert dimmer["battery"] == 87 and st[D_DIMMER].online is True
    dial = st[D_DIAL].state
    assert [b["type"] for b in dial["buttons"]] == ["button", "relative_rotary"]
    assert dial["buttons"][1]["events"] == ["rotate_clock_wise", "rotate_counter_clock_wise"]
    assert dial["bridge_rules"] == sorted([B5, R1])  # the behavior names only the device: all its buttons
    assert B3 not in model(v1=False).button_device_state(D_DIMMER)["bridge_rules"]


def test_inventory_device_info():
    infos = by_id(model().inventory(AT), DeviceInfo)
    assert infos[D_DIMMER].kind == "button" and infos[D_DIMMER].name == "Dimmer gang"
    assert infos[D_DIMMER].model == "RWL022" and len(infos[D_DIMMER].extra["buttons"]) == 4
    assert infos[G_ALL].kind == "light_group" and infos[G_ALL].name == ALL_LIGHTS_NAME
    assert infos[L1].kind == "light" and infos[L1].model == "LCA001"
    assert infos[S_EVENING].kind == "scene" and infos[S_EVENING].extra["grouped_light"] == G_LIVING
    assert all(len(i.device_identity) == 36 for i in infos.values())


def update(*data, cid="evt-1", ctype="update"):
    return {"creationtime": "2026-11-08T13:41:00Z", "id": cid, "type": ctype, "data": list(data)}


def test_light_event_recomputes_group_counts():
    m = model()
    evs = m.apply_container(update({"id": L2, "type": "light", "on": {"on": True}}))
    st = by_id(evs)
    assert st[L2].state["on"] is True and st[L2].state["name"] == "Ledstrip"  # merged with the cache
    assert st[G_LIVING].state["lights_on"] == 2
    assert st[G_SOFA].state["lights_on"] == 1
    assert st[G_ALL].state["lights_on"] == 3
    assert G_BED not in st


def test_connectivity_event_marks_light_unreachable():
    m = model()
    evs = m.apply_container(
        update({"id": Z1, "type": "zigbee_connectivity", "owner": {"rid": D1}, "status": "connectivity_issue"})
    )
    st = by_id(evs)
    assert st[L1].online is False and st[G_LIVING].state["lights_reachable"] == 1


def test_scene_status_changes_active_scene():
    m = model()
    evs = m.apply_container(
        update(
            {"id": S_EVENING, "type": "scene", "status": {"active": "inactive"}},
            {"id": S_BRIGHT, "type": "scene", "status": {"active": "static"}},
        )
    )
    assert by_id(evs)[G_LIVING].state["scene"] == "Helder"


def test_button_event_id_and_order():
    m = model()
    evs = m.apply_container(
        update(
            {"id": G_LIVING, "type": "grouped_light", "on": {"on": False}},
            {
                "id": B1,
                "type": "button",
                "owner": {"rid": D_DIMMER, "rtype": "device"},
                "button": {"button_report": {"event": "short_release", "updated": "2026-11-08T13:41:00.500Z"}},
            },
            cid="9f8e7d6c-0000-4000-8000-000000000001",
        )
    )
    btn = evs[0]  # button presses first: lowest latency for button actions
    assert isinstance(btn, ButtonEvent) and btn.device_identity == D_DIMMER and btn.button == B1
    assert btn.event_id == f"9f8e7d6c-0000-4000-8000-000000000001:{B1}"
    assert btn.observed_at == datetime(2026, 11, 8, 13, 41, 0, 500000, tzinfo=UTC)
    rot = m.apply_container(
        update(
            {
                "id": R1,
                "type": "relative_rotary",
                "relative_rotary": {"rotary_report": {"rotation": {"direction": "counter_clock_wise"}}},
            },
            cid="evt-9",
        )
    )[0]
    assert rot.event == "rotate_counter_clock_wise" and rot.device_identity == D_DIAL and rot.event_id == f"evt-9:{R1}"


def test_rename_add_and_delete():
    m = model()
    infos = by_id(m.apply_container(update({"id": L1, "type": "light", "metadata": {"name": "Hanglamp"}})), DeviceInfo)
    assert infos[L1].name == "Hanglamp"
    gone = m.apply_container(update({"id": S_BRIGHT, "type": "scene"}, ctype="delete"))
    info = next(e for e in gone if isinstance(e, DeviceInfo))
    assert info.removed is True and info.kind == "scene" and info.name == "Helder"
    new = "e" * 8 + "-0000-4000-8000-000000000044"
    evs = m.apply_container(
        update(
            {"id": new, "type": "light", "owner": {"rid": "x"}, "metadata": {"name": "Nieuw"}, "on": {"on": True}},
            ctype="add",
        )
    )
    assert by_id(evs, DeviceInfo)[new].name == "Nieuw" and by_id(evs)[new].state["on"] is True


def test_behavior_change_refreshes_bridge_rules():
    m = model(v1=False)
    evs = m.apply_container(
        update({"id": "5" * 8 + "-0000-4000-8000-000000000002", "type": "behavior_instance", "enabled": True})
    )
    assert B3 in by_id(evs)[D_DIMMER].state["bridge_rules"]
    assert B2 in by_id(evs)[D_DIMMER].state["bridge_rules"]


def test_hue_ids_are_36_characters():
    assert is_hue_id(L1) and len(L1) == 36
    assert is_hue_id("1d6c6a4c-0000-4000-8000-000000000001")
    assert not is_hue_id("1d6c6a4c000040008000000000000001")  # 32 chars
    assert not is_hue_id("../../api/config")
    assert not is_hue_id(None)
    assert B2 != B1
