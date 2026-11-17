"""Beschermde doelen krijgen dezelfde beleidsweigering op alle opslagroutes."""

import pytest

from .boiler_support import commands, mapping, mappings, set_online

pytest_plugins = ["tests.boiler_support"]


@pytest.mark.parametrize("path,scene", [
    ("boiler", None), ("protected_group", None), ("protected_group", "scene"),
    ("group", "boiler"), ("group", "protected_scene"),
])
async def test_lichtroute_beschermd_pad_en_body(admin, boiler_rig, path, scene):
    r = boiler_rig
    body = {"scene_id": str(getattr(r, scene))} if scene else {"on": False}
    response = await admin.post(f"/lights/groups/{getattr(r, path)}", json=body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "policy_denied"
    assert await commands(r.home) == []


@pytest.mark.parametrize("method", ["post", "put", "patch"])
@pytest.mark.parametrize("target", ["boiler", "protected_group", "protected_scene", "zone", "resume"])
async def test_knopopslag_weigert_beschermd_doel_atomair(admin, boiler_rig, method, target):
    r = boiler_rig
    good = mapping({"device_id": str(r.group), "action": "toggle"})
    saved = await admin.post(f"/buttons/{r.button}/mappings", json=good)
    assert saved.status_code == 200, saved.text
    before = await mappings(admin, r)
    if target in ("zone", "resume"):
        step = {"zone_id": str(r.zone), **({"action": "resume"} if target == "resume" else {"mode": "heat"})}
        bad = mapping(step, "climate")
    else:
        bad = mapping({"device_id": str(getattr(r, target)), "action": "recall_scene" if target == "protected_scene" else "toggle"})
    if method == "post":
        bad["button"] = "tweedeknop"
        response = await admin.post(f"/buttons/{r.button}/mappings", json=bad)
    elif method == "put":
        response = await admin.put(f"/buttons/{r.button}/mappings", json=[good, {**bad, "button": "tweedeknop"}])
    else:
        response = await admin.c.patch(f"/api/v1/button-mappings/{before[0]['id']}", json=bad, headers=admin.h())
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "policy_denied"
    assert await mappings(admin, r) == before
    assert await commands(r.home) == []


@pytest.mark.parametrize("online", [True, False])
@pytest.mark.parametrize("body", [{"mode": "heat"}, {"target_c": 22}, {"fan": "high"}])
async def test_zonecommando_bescherming_voor_offline(admin, boiler_rig, online, body):
    r = boiler_rig
    await set_online(r.boiler, online)
    response = await admin.post(f"/zones/{r.zone}/command", json=body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "policy_denied"
    assert await commands(r.home) == []
