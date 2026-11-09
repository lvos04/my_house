# TrueNAS Home Platform V2 — documentatie voor `my_house`

Bron: uitvoeringspakket `TrueNAS_Home_Platform_V2_VOLLEDIG_2026-11-09.zip` (peildatum 9 november 2026).

**Rol van deze repo (vast besluit):** B06 delta op v0.1.0: alleen ontbrekende punten.

Hier staan alleen de eigen B-, I-, A- en promptdocumenten plus de gedeelde architectuur (`00_START`, `01_ARCHITECTUUR`, `05_TESTS`). Het volledige pakket en de Excel staan in `lvos04/ai-server-manager` onder `docs/platform-v2/`.

Onbekende NAS-gegevens (D01–D18) worden nooit met voorbeeldwaarden ingevuld; ze worden via de onboarding-wizard in de web-UI vastgelegd.

## Fase P0 (deze repo)

- `INVENTARIS.md` — wat er in my_house 0.2.0 staat (routes, migraties, auth, events, back-up/herstel, monitoring,
  veiligheidsinvarianten, CI, images, installatiewijze) en de baselinetestrun.
- `DELTA_MATRIX.md` — elke B06-eis plus de relevante I05/A20-stappen en EX-scenario's als bestaand / onvolledig /
  ontbrekend, met bewijs en de geprioriteerde bouwlijst voor fase P3.
- `EVIDENCE.json` — status en validatie per Excel-ID (B06-001…B06-009) volgens §11 van de contracten.
- Keuzes: `docs/adr/ADR-0001` … `ADR-0005`; OpenAPI-momentopname: `docs/openapi/my_house.openapi.json`.

## Bestanden

- `00_START/BESLUITEN_EN_ONBEKENDEN.md`
- `00_START/INHOUDSOPGAVE.md`
- `00_START/INHOUD_CONTROLE.json`
- `00_START/LEES_MIJ_EERST.md`
- `00_START/SHA256SUMS.txt`
- `01_ARCHITECTUUR/01_PROJECTCHARTER.md`
- `01_ARCHITECTUUR/02_DATA_EIGENAARSCHAP.md`
- `01_ARCHITECTUUR/03_RISICO_AUTONOMIE.md`
- `01_ARCHITECTUUR/04_GEGEVENS_EN_STORAGE.md`
- `01_ARCHITECTUUR/05_OPERATIES_EN_UPDATES.md`
- `01_ARCHITECTUUR/06_FASERING_EN_GATES.md`
- `01_ARCHITECTUUR/07_API_EN_EVENTSCHEMA.md`
- `01_ARCHITECTUUR/08_UI_EN_INTERACTIES.md`
- `02_BOUWPLANNEN/B06_MY_HOUSE_DELTA.md`
- `03_INSTALLATIE/I05_MY_HOUSE.md`
- `04_BESTAANDE_APPS/A20_MY_HOUSE.md`
- `05_TESTS/GEBRUIKERSWENSEN_TRACEERBAARHEID.md`
- `05_TESTS/INTEGRALE_E2E_TESTMATRIX.md`
- `06_TEMPLATES/PROMPTS_PER_APP/PROMPT_B06_MY_HOUSE_DELTA.md`
