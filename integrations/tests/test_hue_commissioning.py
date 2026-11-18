"""Ledenmetadata voor de gate blijft gesloten bij een onvolledige Hue-inventaris."""

from datetime import UTC, datetime

import pytest
from hue_inventory import BRIDGE_HOME, D1, G_ALL, G_LIVING, L1, L2, L3, ROOM_LIVING, full_resources

from shc_integrations.base import DeviceInfo, StateUpdate
from shc_integrations.hue import HueModel

AT = datetime(2026, 11, 8, tzinfo=UTC)


def model():
    result = HueModel()
    result.load(full_resources())
    return result


def test_inventaris_en_toestand_noemen_alle_leden():
    m = model()
    assert m.group_state(G_LIVING)["member_identities"] == [L1, L2]
    assert m.group_state(G_ALL)["member_identities"] == [L1, L2, L3]
    assert m.group_state(G_ALL)["members_complete"] is True
    events = m.snapshot(AT)
    for event_type, field in [(DeviceInfo, "extra"), (StateUpdate, "state")]:
        event = next(e for e in events if isinstance(e, event_type) and e.device_identity == G_LIVING)
        assert getattr(event, field)["member_identities"] == [L1, L2]
        assert getattr(event, field)["members_complete"] is True


@pytest.mark.parametrize("defect", ["owner", "children", "device", "services", "light", "cycle", "malformed"])
def test_onbekende_verwijzing_is_geen_geteste_lege_groep(defect):
    m = model()
    if defect == "owner":
        del m.resources[ROOM_LIVING]
    elif defect == "children":
        del m.resources[ROOM_LIVING]["children"]
    elif defect == "device":
        del m.resources[D1]
    elif defect == "services":
        del m.resources[D1]["services"]
    elif defect == "light":
        del m.resources[L1]
    elif defect == "cycle":
        m.resources[ROOM_LIVING]["children"].append({"rid": BRIDGE_HOME, "rtype": "bridge_home"})
    else:
        m.resources[ROOM_LIVING]["children"].append({"rtype": "device"})
    state = m.group_state(G_LIVING)
    assert state["members_complete"] is False, state
    if defect in {"device", "services", "light", "cycle", "malformed"}:
        assert L2 in state["member_identities"]
    if defect == "light":
        assert L1 in state["member_identities"], "Een ontbrekend lid mag niet uit de controle verdwijnen."


def test_lege_bekende_groep_en_wijziging_in_eventstream():
    m = model()
    m.resources[ROOM_LIVING]["children"] = []
    state = m.group_state(G_LIVING)
    assert state["member_identities"] == [] and state["members_complete"] is True
    events = m.apply_container({"type": "update", "creationtime": AT.isoformat(), "data": [{
        "id": ROOM_LIVING, "type": "room", "children": [{"rid": L1, "rtype": "light"}],
    }]})
    state = next(e.state for e in events if isinstance(e, StateUpdate) and e.device_identity == G_LIVING)
    assert state["member_identities"] == [L1] and state["members_complete"] is True
