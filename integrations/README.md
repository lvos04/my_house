# shc-integrations

Lokale apparaat-adapters voor Smart Home Control. Elke adapter implementeert het contract in
`src/shc_integrations/base.py`. Geen cloud, geen Home Assistant-runtime.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q && .venv/bin/ruff check src tests
```

Zie `docs/compatibility.md` voor de status per apparaat.
