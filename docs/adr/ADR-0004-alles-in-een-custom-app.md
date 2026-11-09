# ADR-0004 — Installatie als één standaard TrueNAS Custom App (alles-in-één image)

- **Status:** voorgesteld (ontwerp in P0, bouwen in P3)
- **Context-ID's:** B06-002, B06-008, B06-009, I05-05-01…08, EX15, EX50; `PLATFORM_CONTRACTEN.md` 1.1 §1, §2, §3, §9, §10

## Context

Nu: `install.sh` als root via de TrueNAS-shell, een compose-YAML uit `render_truenas.py` en *Install via YAML*,
met 5–7 containers (db, api, worker, web, mosquitto, optioneel zigbee2mqtt en cloudflared) en drie netwerken met
vaste adressen als vertrouwensgrens. Contract 1.1 verbiedt YAML/compose/terminal als installatiemethode en eist één
image per repo met ingebouwde PostgreSQL op een unix-socket, één datamap `/appdata`, één poort, healthcheck,
geheimen die de app zelf maakt, en processen als 568. Voorbeeld: `media-source-optimizer/deploy/allinone`.

## Besluit (ontwerp)

1. **`deploy/allinone/Dockerfile`**: base `python:3.12.x-slim-trixie@sha256:…`; daarin PostgreSQL 16 (PGDG, zelfde
   major als nu), nginx (de bestaande `nginx.conf.template`, upstream `127.0.0.1:8000`), mosquitto, de gebouwde PWA
   (`apps/web`, build-stage `node:22…@sha256:…`) en de twee Python-pakketten uit het hashed lock-bestand. Sandbox-CA
   alleen als optionele BuildKit-secret (`RUN --mount=type=secret,id=ca_bundle,required=false`), nooit hard.
2. **Procesbewaker** (`supervisor.py`, zoals MSO): volgorde postgres → `shc-api migrate` (met de bestaande
   pre-migratiedump) → api → worker (pas als api gezond) → nginx → mosquitto. Een proces dat stopt, wordt herstart; de
   bestaande worker-waakhond blijft het eigen proces beëindigen bij een vastgelopen lus. Healthcheck: nginx `/healthz`
   + API `/readyz` + liveness-bestand van de worker.
3. **`/appdata`**: `postgres/`, `app/` (data), `backups/`, `secrets/` (0600, 568), `mosquitto/`, `zigbee2mqtt/`.
   **First-start-init** maakt ontbrekende geheimen (hoofdsleutel ≥ 32 tekens, DB-wachtwoord of alleen
   peer-auth via socket, MQTT-wachtwoorden) en **overschrijft nooit** een bestaand geheim. Start als root alleen voor
   `chown 568:568 /appdata/…`, daarna alles als 568, `no-new-privileges`, geen extra capabilities, geen Docker-socket.
4. **Eén poort:** containerpoort 8080 (nginx); hostpoort-voorstel 8088 (ADR-0003 punt 1). Database en MQTT zonder
   netwerkpoort naar buiten (MQTT alleen op 127.0.0.1 binnen de container).
5. **Vertrouwensgrenzen** zonder vaste containernetwerken: API vertrouwt `X-Forwarded-*` alleen van 127.0.0.1 (nginx in
   dezelfde container); de koppel-adresvalidatie weigert loopback en het containernetwerk (nu `SHC_NET_PREFIX`-subnets).
   De tunnel-detectie (`CF-Connecting-IP` alleen van cloudflared) wordt in P3 opnieuw ontworpen omdat cloudflared dan
   geen vast intern adres meer heeft — tot dat ontwerp er is, is de tunnel **uit** in de alles-in-één image.
6. **Zigbee2MQTT** (Node.js, USB-dongle): **open punt voor P3**, twee varianten, beide met `/dev/serial/by-id/…`:
   (a) optioneel proces in dezelfde image, aan als in het formulier een USB-apparaat is doorgegeven; (b) aparte
   standaard Custom App of catalogusapp die met de broker in my_house praat (vraagt een MQTT-poort op de host met
   eigen wachtwoord). Of het TrueNAS-formulier van de echte versie (D01) apparaat-doorgifte biedt, is **NIET
   GEVALIDEERD (echte NAS)** en moet tegen de officiële TrueNAS-documentatie worden gecontroleerd.
7. **Cloudflared**: niet in de image; optioneel als catalogusapp of later via de netwerkbouwplannen (B10). Een
   tunneltoken in een formulier is een geheim (contract §3) — dat besluit ligt bij B10/de gebruiker.
8. **Bestaande compose-installaties migreren zonder shell:** (1) in de oude app een volledige versleutelde back-up
   maken en downloaden (bestaande route); (2) de nieuwe Custom App installeren; (3) daar *Herstellen › zelfde woning*
   kiezen en de back-up uploaden (bestaande `restore/verify` + `apply`, veilige modus erna). Apparaatsleutels zijn
   versleuteld met de oude hoofdsleutel: de herstelcontrole meldt dat al (`credentials`), en P3 voegt een
   UI-route toe om de oude hoofdsleutel eenmalig te uploaden (nu alleen `shc-api rekey`) of meldt *opnieuw koppelen*.
   De oude app blijft tot de controle klaar is (A20: "in één handeling terug naar de oude app").
9. **Updaten/terugdraaien** via het formulier (andere tag). Een oudere image die een **nieuwer schema** aantreft, start
   niet blind maar toont een Nederlandse herstelpagina (EX15) met de pre-update-back-up als weg terug.
10. **Manifest** `deploy/manifest/my_house.manifest.yaml` (§9: `install.method: custom_app`, image + digest door
    release-CI, `privileges.privileged:false`, `devices` alleen voor de optionele dongle met `privilege_reason`,
    `backup.db_export` = bestaande dump, `decisions_required: [D01, D02, D03, D12, D13, D14]`,
    `validation_status: "NIET GEVALIDEERD (echte NAS)"`), `deploy/truenas/CUSTOM_APP.md` (formuliervelden NL) en
    `.env.example` (alleen ontwikkeling). `infra/compose` verhuist naar `deploy/dev/` met de expliciete melding
    *geen installatiemethode*.
11. **CI:** de alles-in-één image bouwen, starten (als root én als 568), healthcheck, de bestaande `smoke_test.py` en de
    browsertests tegen die ene container, herstart/harde kill, back-up en herstel naar een tweede container met een
    ander IP (EX50). Release: geen `:latest` meer, digests in `release/digests.json`.

## Afgewezen alternatieven

- **Huidige compose-stack via Install via YAML behouden:** strijdig met contract 1.1.
- **Elke dienst een eigen Custom App** (db, api, worker, web, mosquitto): meer formulieren, gedeelde geheimen en
  onderlinge netwerkadressen; strijdig met "één image per repo".

## Gevolgen

- Grootste P3-hiaat (P3-01 in `DELTA_MATRIX.md` §9). Tot het klaar is, is my_house **niet** conform installeerbaar.
- De bestaande veiligheidslagen (netwerkscheiding, read-only rootfs, geheimen per proces) moeten een gelijkwaardige
  vervanger krijgen binnen één container (aparte gebruikers per proces waar zinvol, bestandsrechten 0600/0640); dat is
  onderdeel van de P3-review.
