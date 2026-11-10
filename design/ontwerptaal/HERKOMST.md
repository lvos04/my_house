# Herkomst van `design/ontwerptaal/` (O-14, ONTWERPTAAL §14)

Versie-gepinde kopie van de ontwerptaal **Peil 1.1.1** (10 november 2026, design-agent; patchversie met de
verbeterpunten E01–E16 uit de ontwerpreviews, waaronder die van my_house). Bron van waarheid:
`lvos04/ai-server-manager` — `docs/platform-v2/ONTWERPTAAL.md`, `design/ontwerptaal/` en
`docs/adr/ADR-0015-ontwerptaal-peil.md` (contract 1.3 §13).

**Pin (E08):** versie én commit-SHA van de bron, in [`HERKOMST.json`](HERKOMST.json): Peil `1.1.1`, bron-commit
`4ff4b3ff660b6ad3abf5979549c0e4405ecb8437` op de werkbranch `ccr-c133d7a0-kuz1cv` (Peil staat niet op `main`; een
vergelijking met `main` zou vals alarm geven). Tot 1.1.1 was dit een kopie van 1.1.0 uit de map `definitief/` van de
ontwerptaal (buiten de repos, 9 november 2026).

| Bestand | Bron in ai-server-manager | Gebruik in my_house | sha256 van het bronbestand |
|---|---|---|---|
| `aliaslagen/my_house.css` | `design/ontwerptaal/aliaslagen/my_house.css` | kopie in de app: `apps/web/src/peil-alias.css` (laag legacy) | `643584d4436c6eeba8e19a514b02dcda42dbe1c35a1f79515966ffa273bb46b7` |
| `ontwerptaal.css` | `design/ontwerptaal/ontwerptaal.css` | kopie in de app: `apps/web/src/peil/ontwerptaal.css` (Tailwind 4 `@theme` + basislaag) | `a69a46d2ccd2eb499511734529d551946844ea00a9d9996fe2acd6bf76cdf0c8` |
| `tokens.json` | `design/ontwerptaal/tokens.json` | Android: bron van `PeilTokens.kt` (`apps/android/tools/peil_tokens.py`) | `cc03fcd019b43d9ee52509c7812eff38a726de80a6cf4041b82593c52e88fb0e` |
| `components.json` | `design/ontwerptaal/components.json` | advies voor de shadcn-CLI (PEIL_MIGRATIE §8.2) | `831fdce06c6c25c7f67d23b737f27184bca9e4a0ff5c0a60175a6a58edbe4c7e` |
| `ONTWERPTAAL.md` | `docs/platform-v2/ONTWERPTAAL.md` | regels (bindend) | `79130d3e7e3002a3205b4a02c565d45cc61ba68a2bea9bf4a6b385e3ab8163db` |
| `COMPONENTEN.md` | `design/ontwerptaal/COMPONENTEN.md` | componentspecificaties (handoff) | `9172bf053cedc4821e08ae14e13dc04ddf64ffd253e6fb9b62f2239fdd3cd17c` |
| `ADR-ONTWERPTAAL.md` | `docs/adr/ADR-0015-ontwerptaal-peil.md` | het besluit en de alternatieven | `af350a471658a22669b18058b21314d731226a23245f8af4874534f6e2808ac8` |

- `.css`- en `.md`-bestanden hebben een **herkomstkop van 2 regels** bovenaan (versie, bron-commit, bronpad en de
  sha256 van het bronbestand); de rest is byte-gelijk aan de bron op de gepinde commit. `tokens.json` en
  `components.json` kunnen geen kop dragen en zijn byte-gelijk. Relatieve links in de `.md`-kopieën wijzen naar de
  mappen van de bronrepo; een kopie wordt nooit aangepast om een link te repareren.
- Niet gekopieerd (te groot of niet nodig in deze repo): `prototypes/`, `shots/`, `bouw/`, `controle/`, `contrast.json`,
  `statuspagina.*` (my_house heeft geen platform-statuspagina; contract 1.3 §13) en de andere aliaslagen.

## Bewaking

`tools/peil_kopie.py` (alleen de standaardbibliotheek):

```sh
python3 tools/peil_kopie.py controleer                        # offline: koppen, inhoud, pin, app-kopieën, PeilTokens.kt
python3 tools/peil_kopie.py vergelijk --online                 # tegen GitHub (publieke bronrepo)
python3 tools/peil_kopie.py vergelijk --bron ../ai-server-manager   # tegen een lokale checkout
```

- **Fout (CI rood):** een kopie wijkt af van de bron op de gepinde commit; de huidige bron heeft **dezelfde versie met
  andere inhoud**; een kopie in de app wijkt af van de gepinde kopie; `PeilTokens.kt` is niet uit de gepinde
  `tokens.json` gemaakt.
- **Waarschuwing (CI groen):** de bron heeft een **nieuwere** Peil-versie. Bijwerken is een bewuste stap:
  `python3 tools/peil_kopie.py bijwerken --bron <checkout> --commit <40 hex> --ref <branch>`, daarna
  `python3 apps/android/tools/peil_tokens.py`, de web-, browser- en Android-tests (Paparazzi opnieuw vastleggen als het
  uiterlijk verandert) en een korte ontwerpreview (`docs/ontwerp/PEIL_REVIEW.md`).

De CI draait `controleer` en `vergelijk --online` in de backend-job; de pytest-tests staan in
`apps/api/tests/platform_v2/test_peil_kopie.py` (met negatieve gevallen) en de webtest in
`apps/web/src/test/peil-herkomst.test.ts`. De Android-CI draait `peil_tokens.py --check` en `PeilTokensTest`.

## Wat 1.1.1 in my_house verandert

Zie `docs/ontwerp/PEIL_MIGRATIE.md` §17 en ADR-0012: de `-soft`-banden van verwarmen en koelen (tekst in `-ink` erop
haalt nu ≥ 7:1), de rand van een gekozen modus en een brandende lichtgroep in `-ink`, de ondernavigatie op labelbreedte
zonder vet actief item (web en Android) en de my_house-correcties uit PEIL_MIGRATIE §8.1 in de aliaslaag zelf.
