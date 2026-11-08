# shc-integrations

Lokale apparaat-adapters voor Smart Home Control. Elke adapter implementeert het contract in
`src/shc_integrations/base.py`. Geen cloud, geen Home Assistant-runtime.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q && .venv/bin/ruff check src tests
```

Zie `docs/compatibility.md` voor de status per apparaat.

## Contract in het kort

- `poll()` / `subscribe()` leveren `Reading`, `StateUpdate`, `ButtonEvent` en `DeviceInfo`.
  `DeviceInfo` is inventaris (soort, naam, model, firmware, `removed`) zonder meting of online-stand;
  de worker maakt daarmee een apparaat met de juiste soort aan of werkt een algemene soort bij
  (`should_upgrade_kind`).
- `health()` geeft `Health(state, detail, code, extra)`: `code` is een foutcode zoals bij
  `AdapterError.code`, `extra` bevat JSON-velden per adapter (bijvoorbeeld `mqtt_connected` en
  `bridge_online` bij Zigbee, `connected_since` en `last_event_at` bij Hue).
- Fouten zijn `AdapterError`-subklassen met een `code` (`device_offline`, `unsupported`,
  `pairing_required`, `policy_denied`, `identity_mismatch`, `certificate_mismatch`,
  `protocol_error`) en optioneel `diagnostics` (JSON, zonder geheimen).
- `device_info()`, `inventory()`, `climate_capabilities()` (contract C1 voor airco's) en
  `diagnostics()` hebben een standaardimplementatie in `Adapter`.

## Een nieuwe adapter testen

Maak een nep-apparaat dat het echte protocol spreekt (zie `tests/fake_gree.py` en
`tests/fake_http.py`) en laat de adapter de vaste contracttests draaien:

```python
from contract_kit import AdapterContract, ContractEnv

class TestMijnAdapterContract(AdapterContract):
    @pytest.fixture
    async def env(self):
        ...  # start nep-apparaat, koppel de adapter
        yield ContractEnv(adapter=..., device_identity=..., supported_command=...,
                          unsupported_command=..., unreachable=..., unconfigured=...)
```
