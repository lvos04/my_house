# Herkomst van `design/ontwerptaal/` (O-14)

Versie-gepinde kopie van de ontwerptaal **Peil 1.1.0** (9 november 2026, design-agent; goedgekeurd na de onafhankelijke
ontwerpreview 1.0.0 → 1.1.0). De bron van waarheid wordt na de overdracht
`ai-server-manager/docs/platform-v2/ONTWERPTAAL.md` + `ai-server-manager/design/ontwerptaal/` (contract 1.2 §13). Tot die
commit bestaat staat de bron in de map `definitief/` van de ontwerptaal (buiten de repos).

**Status in my_house:** de omzetting staat in [`docs/ontwerp/PEIL_MIGRATIE.md`](../../docs/ontwerp/PEIL_MIGRATIE.md).
De web-app gebruikt kopieën met herkomstkop in `apps/web/src/` (§14 daar). De Android-app genereert zijn kleuren en
typografie uit `tokens.json` hieronder (`apps/android/tools/peil_tokens.py` → `PeilTokens.kt`, met dezelfde sha256 in de
kop); `PeilTokensTest` en de CI-stap `peil_tokens.py --check` falen als die niet meer overeenkomen (ADR-0010).

| Bestand | Gebruik in my_house | sha256 van het bronbestand |
|---|---|---|
| `aliaslagen/my_house.css` | fase 1: na `apps/web/src/styles.css` importeren | `18495346cb556e48753c99260424338ce344ef17d92003655c39c69988f8c302` |
| `ontwerptaal.css` | fase 2: Tailwind 4 `@theme` + shadcn-variabelen | `b384b3d074c7c892d8e5a8320b1bdcd3d8612d98b3a05daa9a9e6d12560d020e` |
| `tokens.json` | Android: bron voor de Compose-kleuren/typografie/vormen (DTCG) | `9583506d8585ba39486f75d4c27f8e40f7878104674c27e229d10f92013a30e1` |
| `components.json` | fase 2: advies voor de shadcn-CLI (paden aanpassen, zie migratie §8) | `831fdce06c6c25c7f67d23b737f27184bca9e4a0ff5c0a60175a6a58edbe4c7e` |
| `ONTWERPTAAL.md` | regels (bindend) | `a957370b1eceac49ab9f47204c252a67d15967ebae240eb33440b8963898d33b` |
| `COMPONENTEN.md` | componentspecificaties (handoff) | `d0b0999d8f07537fcc4447d30ab5a3d45632d559487736082beae8a3c28b0154` |
| `ADR-ONTWERPTAAL.md` | het besluit en de alternatieven | `df302eb5a727b5649dafec1c0c0d469cf5bfc39fd32b3834c415aaa805db05ac` |

- `.css`- en `.md`-bestanden hebben een **herkomstkop van 2 regels** bovenaan; de rest is byte-gelijk aan de bron.
  `tokens.json` en `components.json` kunnen geen kop dragen en zijn byte-gelijk.
- Niet gekopieerd (te groot of niet nodig in deze repo): `prototypes/`, `shots/`, `bouw/`, `controle/`, `contrast.json`,
  `statuspagina.*` (my_house heeft geen platform-statuspagina) en de andere aliaslagen.

## Controleren

```sh
cd design/ontwerptaal
for f in aliaslagen/my_house.css ontwerptaal.css ONTWERPTAAL.md COMPONENTEN.md ADR-ONTWERPTAAL.md; do
  echo "$(tail -n +3 "$f" | sha256sum | cut -d' ' -f1)  $f"; done
sha256sum tokens.json components.json
```

De uitkomst moet gelijk zijn aan de tabel. Een CI-stap die dit vergelijkt met de versie in `ai-server-manager` komt
pas als die bron gecommit is (ONTWERPTAAL §14); tot dan is dit een handmatige controle.
