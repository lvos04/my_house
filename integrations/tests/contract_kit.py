"""Reusable contract tests for device adapters (item 42).

Every adapter, including future ones, runs the same checks against a fake device that speaks
the real protocol on 127.0.0.1 (no mocks inside the adapter). The checks enforce the rules
from `base.py` and the plan: capabilities are declared, health never fakes "ok", an action
the adapter does not implement raises `Unsupported`, an unreachable device is an error and
not a result, and a command only counts as confirmed after a readback showed it.

Usage in a test module:

    class TestMyAdapterContract(AdapterContract):
        @pytest.fixture
        async def env(self):
            ...start a fake device, pair the adapter...
            yield ContractEnv(adapter=..., device_identity=..., ...)
            ...stop the fake device...
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import pytest

from shc_integrations.base import (
    Adapter,
    AdapterError,
    ButtonEvent,
    Cap,
    Command,
    CommandResult,
    DeviceInfo,
    DiscoveredDevice,
    Event,
    Health,
    HealthState,
    Quality,
    Reading,
    StateUpdate,
    Unsupported,
)
from shc_integrations.registry import ADAPTERS

C1_KEYS = {"modes", "min_c", "max_c", "step_c", "fan", "swing"}
C1_MODES = {"off", "heat", "cool", "dry", "fan", "auto"}


@dataclass
class ContractEnv:
    adapter: Adapter  # paired, talking to a working fake device
    device_identity: str
    supported_command: Command | None  # a command the fake device obeys
    unsupported_command: Command  # an action this adapter does not implement
    unreachable: Adapter  # same adapter type, configured for an address where nothing answers
    unconfigured: Adapter  # not paired / no address
    expect_confirmed: bool = True  # adapters without readback must return confirmed=False
    stop_obeying: Callable[[], None] | None = None  # the fake device ignores commands from now on
    expected_caps: set[Cap] | None = None
    unreachable_command: Command | None = None  # defaults to supported_command
    polls: bool = True  # False for event-driven adapters whose poll() returns []
    sample_events: list[Event] = field(default_factory=list)  # events from the push path, if any
    discovered: DiscoveredDevice | None = None  # what probe/pair reported


def assert_event_well_formed(ev: Event) -> None:
    assert isinstance(ev, (Reading, StateUpdate, ButtonEvent, DeviceInfo)), f"onbekend eventtype {type(ev)}"
    assert isinstance(ev.device_identity, str) and ev.device_identity, "lege apparaat-identiteit"
    assert isinstance(ev.observed_at, datetime) and ev.observed_at.tzinfo is not None, "tijd zonder tijdzone"
    if isinstance(ev, Reading):
        assert ev.metric and ev.unit
        assert isinstance(ev.quality, Quality)
        # No fake values: a missing value is flagged as missing, and a flagged value is absent.
        assert (ev.value is None) == (ev.quality == Quality.MISSING), f"{ev.metric}: waarde/kwaliteit klopt niet"
    elif isinstance(ev, StateUpdate):
        assert isinstance(ev.state, dict) and isinstance(ev.online, bool)
        json.dumps(ev.state, default=str)
    elif isinstance(ev, ButtonEvent):
        assert ev.button and ev.event
    else:
        assert ev.kind, "DeviceInfo zonder soort"
        json.dumps(ev.extra, default=str)


def assert_health_well_formed(h: Health) -> None:
    assert isinstance(h, Health) and isinstance(h.state, HealthState)
    assert isinstance(h.detail, str) and h.detail, "gezondheid zonder uitleg"
    assert h.checked_at.tzinfo is not None
    assert isinstance(h.extra, dict)
    json.dumps(h.extra)  # the API stores/serves it as JSON
    if h.state == HealthState.OK:
        assert h.code is None


NOT_SECRET = {"cert_fingerprint", "bridge_id"}  # identities stored next to the secrets


def _secrets(adapter: Adapter) -> list[str]:
    creds = getattr(adapter, "credentials", {}) or {}
    return [v for k, v in creds.items() if isinstance(v, str) and len(v) >= 6 and k not in NOT_SECRET]


class AdapterContract:
    """Subclass and provide an `env` fixture yielding a ContractEnv."""

    async def test_registered_provider(self, env: ContractEnv):
        assert ADAPTERS.get(env.adapter.provider) is type(env.adapter)

    async def test_dutch_instructions(self, env: ContractEnv):
        steps = env.adapter.instructions().steps
        assert steps and all(isinstance(s, str) and s.strip() for s in steps)

    async def test_capabilities_are_declared_caps(self, env: ContractEnv):
        caps = await env.adapter.get_capabilities(env.device_identity)
        assert isinstance(caps, set) and all(isinstance(c, Cap) for c in caps)
        if env.expected_caps is not None:
            assert caps == env.expected_caps

    async def test_climate_capabilities_follow_c1(self, env: ContractEnv):
        caps = env.adapter.climate_capabilities(env.device_identity)
        if caps is None:
            return
        assert set(caps) == C1_KEYS
        assert set(caps["modes"]) <= C1_MODES and "off" in caps["modes"]
        assert caps["min_c"] < caps["max_c"] and caps["step_c"] in (0.5, 1.0)
        assert isinstance(caps["fan"], bool) and isinstance(caps["swing"], bool)

    async def test_events_are_well_formed(self, env: ContractEnv):
        events = list(await env.adapter.poll()) + list(env.sample_events)
        if env.polls:
            assert events, "poll() gaf niets terug van een werkend apparaat"
        else:
            assert await env.adapter.poll() == []
        for ev in events:
            assert_event_well_formed(ev)

    async def test_health_ok_when_reachable(self, env: ContractEnv):
        h = await env.adapter.health()
        assert_health_well_formed(h)
        assert h.state == HealthState.OK, h.detail

    async def test_health_never_ok_when_unconfigured(self, env: ContractEnv):
        h = await env.unconfigured.health()
        assert_health_well_formed(h)
        assert h.state in (HealthState.NOT_CONFIGURED, HealthState.ERROR, HealthState.WARN)

    async def test_unreachable_device_is_an_error_not_a_result(self, env: ContractEnv):
        if env.polls:
            with pytest.raises(AdapterError):
                await env.unreachable.poll()
        cmd = env.unreachable_command or env.supported_command
        if cmd is not None:
            with pytest.raises(AdapterError):
                await env.unreachable.execute(cmd)
        h = await env.unreachable.health()  # must not raise
        assert_health_well_formed(h)
        assert h.state != HealthState.OK
        assert h.code, "fout zonder foutcode"
        await env.unreachable.close()

    async def test_unsupported_command_raises(self, env: ContractEnv):
        with pytest.raises(Unsupported):
            await env.adapter.execute(env.unsupported_command)

    async def test_supported_command_is_honest(self, env: ContractEnv):
        if env.supported_command is None:
            pytest.skip("adapter heeft geen commando's")
        res = await env.adapter.execute(env.supported_command)
        assert isinstance(res, CommandResult)
        assert res.ok is True
        assert res.confirmed is env.expect_confirmed
        assert isinstance(res.detail, str)

    async def test_no_confirmation_without_matching_readback(self, env: ContractEnv):
        if env.stop_obeying is None or env.supported_command is None:
            pytest.skip("geen readback om te vervalsen")
        env.stop_obeying()
        res = await env.adapter.execute(env.supported_command)
        assert res.confirmed is False
        assert res.detail, "niet bevestigd zonder uitleg"

    async def test_diagnostics_serialisable_without_secrets(self, env: ContractEnv):
        await env.adapter.poll()
        text = json.dumps(env.adapter.diagnostics(), default=str)
        text += json.dumps((await env.adapter.health()).extra)
        for secret in _secrets(env.adapter):
            assert secret not in text, "geheim in diagnose of gezondheid"

    async def test_identity_is_never_an_ip(self, env: ContractEnv):
        idents = [env.device_identity] + ([env.discovered.stable_identity] if env.discovered else [])
        for ident in idents:
            host = ident.rsplit(":", 1)[0] if ident.count(":") == 1 else ident
            try:
                ipaddress.ip_address(host)
            except ValueError:
                continue
            pytest.fail(f"identiteit {ident} is een IP-adres")
