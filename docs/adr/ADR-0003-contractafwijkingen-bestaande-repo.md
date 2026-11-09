# ADR-0003 — Contractafwijkingen voor een bestaande app (my_house)

- **Status:** voorgesteld (P0); te bevestigen door de eigenaar van `PLATFORM_CONTRACTEN.md` (`ai-server-manager`)
- **Context-ID's:** B06-006, B06-009, I05; `PLATFORM_CONTRACTEN.md` 1.1 §1, §2, §5, §10, §12

## Context

`PLATFORM_CONTRACTEN.md` 1.1 beschrijft vooral **nieuwe** diensten. my_house is een bestaande app met eigen web-app
(React/Vite PWA), Android-app en eigen API-conventies. Contract §12: bestaande integriteit, rollback en monitoring
nooit dubbel bouwen, vervangen of uitschakelen. Op enkele punten zwijgt het contract of botst een letterlijke
toepassing met bestaande clients.

## Besluiten (veiligste interpretatie)

| # | Onderwerp | Contract | Besluit voor my_house |
|---|---|---|---|
| 1 | **Ontbrekende rij in §2** (app-naam, image, poort) | my_house staat niet in de tabel | Voorstel: Custom App-naam `smart-home` (al gebruikt in `docs/installatie-truenas.md`; TrueNAS-appnamen zonder `_`), image `ghcr.io/lvos04/my_house` (alles-in-één, nieuw), containerpoort `8080`, hostpoort-voorstel **`8088`** (bestaande installaties en gekoppelde telefoons bewaren het thuisadres mét poort in de QR-koppeling; een andere poort breekt die). De Hub controleert conflicten vóór installatie. Eigenaar contract moet de rij toevoegen |
| 2 | **UI** | Alle schermen in de Hub-UI; andere nieuwe diensten alleen een statuspagina | Geldt voor *nieuwe* diensten. my_house **behoudt** zijn eigen web-app en Android-app (bestaande gebruikersreizen, QR-koppeling, widget). De Hub toont een huiskaart + deep-link (B06-006); geen kopie van my_house-schermen in de Hub |
| 3 | **Foutformaat** §5 | `{code,message,details,trace_id}` | Alleen op nieuwe routes (`/connector/v1/*`, `/healthz`, `/readyz`). Bestaande routes houden `{"detail":{"code","message"}}`; de Hub-adapter vertaalt |
| 4 | **`Idempotency-Key` verplicht** §5 | op alle mutaties | Verplicht op de connector-mutaties (propose/apply). Op bestaande routes niet verplicht maken (zou web/Android breken); bestaande commando's zijn al via `command_id` traceerbaar |
| 5 | **Paginering/ETag** §5 | cursor/limit, ETag | Op connector-`items`/`events`; bestaande lijsten blijven zoals ze zijn |
| 6 | **Rollen** §4 | `admin/operator/viewer/kind` | my_house houdt `admin/viewer` (gebruikers) en `admin/control/viewer` (telefoons). Hub-rollen worden niet 1-op-1 doorgezet; de Hub bedient alleen via ondertekende verzoeken (ADR-0002) en my_house legt `actor` in zijn audit vast |
| 7 | **uid** §1/§10 | alles als 568 na eigenaarschapsstap | In de alles-in-één image (ADR-0004) als 568; de bestaande compose-images (10001/70/1883/101) blijven alleen voor ontwikkeling/CI |
| 8 | **`docker compose`** §1 | alleen ontwikkeling/CI/E2E | `infra/compose` + `render_truenas.py` + `install.sh` worden **geen** productie-installatiemethode meer; ze blijven voor CI/ontwikkeling tot de alles-in-één image hun CI-dekking (rooktest, browsertests) overneemt. B06-009 ("full docker compose") en I05 stap 1–3 ("install.sh", "YAML", "Install via YAML") worden gelezen als: hardwaretest met de **alles-in-één Custom App** |
| 9 | **Terminal** | geen terminal voor de gebruiker | Updaten/terugdraaien, wachtwoord vergeten en sleutelwissel moeten zonder shell kunnen (P3); tot dan staan de CLI-procedures in `docs/onderhoud.md` als *noodprocedure*, niet als normale weg |
| 10 | **B06-003 "altijd policy_denied"** | — | Elke bedieningspoging op een beschermd apparaat geeft `409 policy_denied`; nu geven sommige routes 404/422 omdat het doel geen geldig type is (geen veiligheidsgat). In P3 gelijktrekken (ADR-0002 punt 5) |

## Gevolgen

- `contract_issues` in het P0-rapport: punten 1, 2, 3/4, 8 en 9.
- Geen bestaande clientcontracten breken; nieuwe platformroutes volgen het contract volledig.
