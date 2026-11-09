"""Kleine punten "Hue-scènes": a full reload reports what was deleted on the bridge meanwhile.

Deletions arrive as eventstream "delete" containers; while the stream is down they are missed. The
full reload after every (re)connect therefore compares with the previous inventory (or, after a
restart, with what the worker seeded) and emits ``DeviceInfo(removed=True)`` for what is gone."""

import respx
from conftest import no_sleep
from hue_inventory import D3, D_DIMMER, G_BED, L1, L3, S_NIGHT, full_resources

from shc_integrations.base import DeviceInfo, StateUpdate
from shc_integrations.hue import HueAdapter

HOST = "192.168.1.30"
BASE = f"https://{HOST}"
CREDS = {"application_key": "k", "cert_fingerprint": "ab" * 32, "bridge_id": "ecb5fafffe000001"}


def make(plain_factory):
    return HueAdapter({"host": HOST}, dict(CREDS), http_factory=plain_factory, sleep=no_sleep)


def serve(resources):
    respx.get(f"{BASE}/clip/v2/resource").respond(json={"errors": [], "data": resources})
    respx.get(f"{BASE}/api/k/rules").respond(json={})


def removed(evs):
    return {e.device_identity: e for e in evs if isinstance(e, DeviceInfo) and e.removed}


def without(resources, *ids):
    return [r for r in resources if r["id"] not in ids]


@respx.mock
async def test_full_reload_reports_deleted_light_scene_and_button(plain_factory):
    a = make(plain_factory)
    serve(full_resources())
    first = await a.full_reload_events()
    assert not removed(first)  # nothing known before the first load
    # Deleted on the bridge while the eventstream was down: the night light (its device too), the
    # "Nacht" scene and the dimmer switch.
    serve(without(full_resources(), L3, D3, S_NIGHT, D_DIMMER))
    second = await a.full_reload_events()
    gone = removed(second)
    assert set(gone) == {L3, S_NIGHT, D_DIMMER}
    assert gone[L3].kind == "light" and gone[S_NIGHT].kind == "scene" and gone[D_DIMMER].kind == "button"
    assert gone[L3].name == "Nachtlamp"
    assert second.index(gone[L3]) < len(gone)  # removals first, then inventory and state
    assert L1 in {e.device_identity for e in second if isinstance(e, DeviceInfo) and not e.removed}
    # Reported once: the next reload compares with the new inventory.
    assert not removed(await a.full_reload_events())


@respx.mock
async def test_seeded_inventory_catches_deletions_made_while_the_worker_was_down(plain_factory):
    a = make(plain_factory)
    # What the app knows of this bridge (the worker seeds it at start); the bridge device itself is
    # no inventory item and must never be reported as removed.
    a.seed_inventory(
        {
            L1: ("light", "Plafond"),
            "99999999-0000-4000-8000-000000000001": ("light", "Oude lamp"),
            G_BED: ("light_group", "Slaapkamer"),
            "ecb5fafffe000001": ("hue_bridge", "Hue Bridge"),
        }
    )
    serve(full_resources())
    gone = removed(await a.full_reload_events())
    assert set(gone) == {"99999999-0000-4000-8000-000000000001"}
    # Seeding after a reload changes nothing (the adapter's own inventory is newer).
    a.seed_inventory({"88888888-0000-4000-8000-000000000001": ("light", "x")})
    assert not removed(await a.full_reload_events())


@respx.mock
async def test_an_empty_answer_archives_nothing(plain_factory):
    a = make(plain_factory)
    serve(full_resources())
    await a.full_reload_events()
    serve([])
    assert not removed(await a.full_reload_events())
    serve(without(full_resources(), L3))
    assert set(removed(await a.full_reload_events())) == {L3}  # compared with the last real inventory


@respx.mock
async def test_a_removed_button_device_gets_no_state_update_that_revives_it(plain_factory):
    """The device is gone but its button services are still in the (partial) list: the reload reports it
    as removed and sends no state for it, or the worker would make it active again in the same batch."""
    a = make(plain_factory)
    serve(full_resources())
    await a.full_reload_events()
    serve(without(full_resources(), D_DIMMER))
    evs = await a.full_reload_events()
    assert set(removed(evs)) == {D_DIMMER}
    assert not [e for e in evs if isinstance(e, StateUpdate) and e.device_identity == D_DIMMER]
