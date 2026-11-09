# Inventaris — my_house (Smart Home Control) — fase P0 / B06-001

Peildatum **2026-11-09** · repo `lvos04/my_house` · branch `ccr-c133d7a0-kuz1cv` · code-HEAD **`b0a6a98`**
("Smart Home Control v0.2.0 (#3)"); de drie commits daarna (`f82e1d5`, `d007971`, `9c9d02f`) bevatten alleen
`docs/platform-v2/`. `VERSION` = **0.2.0**.

Dit document beschrijft wat er **nu in de repository en op GitHub** staat. Het zegt niets over de echte NAS: of en
welke my_house-versie daar draait, met welke images, datasets, poorten en apparaten (D01, D02, D03, D07, D12–D14), is
**onbekend** en in deze bouwomgeving niet te meten. Alles wat hieronder "getest" heet, is getest met nep-apparaten,
simulators of alleen statisch nagelezen — **NIET GEVALIDEERD (echte NAS / hardware)**.

Methode: statische inspectie van de code (AST-extractie van alle FastAPI-routes met hun minimale toegangsniveau,
`grep` op bestand:regel), alleen-lezen controles op GitHub (`git ls-remote`, `gh api repos/lvos04/my_house/…`) en
GHCR (`docker buildx imagetools inspect`), een OpenAPI-export van HEAD (`docs/openapi/my_house.openapi.json`) en een
echte testrun van de bestaande suites zoals de CI ze draait (§15). In deze fase is **geen productcode** gewijzigd.

Markeringen: **[INTEGRITEIT]**, **[ROLLBACK]** en **[MONITORING]** = bestaande voorzieningen die volgens
`PLATFORM_CONTRACTEN.md` §12 **nooit dubbel gebouwd, vervangen of uitgeschakeld** mogen worden (samenvatting §13).

---

## 1. Versie- en releasestatus

| Wat | Feit (2026-11-09) | Bron |
|---|---|---|
| `VERSION` | `0.2.0` (enige versiebron: `config.py` leest `/app/VERSION` in de image, web krijgt `APP_VERSION` als build-arg) | `VERSION`, `apps/api/Dockerfile:64`, `apps/web/Dockerfile:10` |
| Git-tags op GitHub | alleen `v0.1.0` → `4ed22bc` | `git ls-remote --tags origin` |
| GitHub-releases | alleen **v0.1.0** (2026-11-08, asset `huis-v0.1.0.apk`, images `ghcr.io/lvos04/my_house-api:0.1.0` en `-web:0.1.0`) | `gh api repos/lvos04/my_house/releases` |
| Release-run 0.2.0 | run **42580532662** op `main` (merge `b0a6a98`) **mislukt**: job `apk` → stap *Unit tests* faalde door een netwerkfout (Gradle-distributie HTTP **504**), daardoor `images` en `publish` **overgeslagen**. Er is dus **geen** tag `v0.2.0`, **geen** GitHub-release 0.2.0 en (volgens de workflowlogica) **geen** image `:0.2.0` gepubliceerd | `gh api …/actions/runs/42580532662/jobs` + joblog 129037971278 |
| Release-run 0.1.0 | run 42507969836 geslaagd | `gh run list --workflow release.yml` |
| CI op deze branch | run **42587407462** op `9c9d02f` geslaagd: `backend`, `web`, `e2e`, `compose`, `images, scan and smoke test` | `gh api …/actions/runs/42587407462/jobs` |
| GHCR | `ghcr.io/lvos04/my_house-api` en `-web` zijn **niet anoniem leesbaar** (401 bij `imagetools inspect` voor `:0.1.0`, `:0.2.0`, `:latest`, `:edge`): privé of niet aanwezig — digest **onbekend** | `docker buildx imagetools inspect` |
| Wat draait er op de NAS | **onbekend** (D07) | — |

**Wat 0.2.0 ten opzichte van de pakketreferentie v0.1.0 al toevoegde** (`docs/release-notes.md:12-21`, commits
`56b43ff`…`36b244d`, 318 bestanden): installatiewizard met *Controles vooraf* (`GET /system/preflight`), helppagina per
fabrikant; weekschema's met modus per blok, comfortprofielen, waakhond per zone, experimenten met automatisch
profielwissel; commando's pas "bevestigd" na readback, beperkte herhaalpogingen; energie-uurtotalen, plausibiliteit,
correcte zomer-/wintertijd, T1/T2 + gas, kwaliteitslabels, gestreamde export; automatische versleutelde back-ups +
database-dump, inhalen na uitval, herstel en verhuisassistent in de app, `shc-api restore-drill`; melding bij
stilgevallen worker + optionele push (ntfy); gebruikersbeheer + logboek, telefoonrollen, tokens die na 180 dagen
verlopen, beheer van gebruikers/telefoons/back-ups alleen via web-sessie, serverherkenning, certificaatpinning,
drie gescheiden containernetwerken; Hue-knoppen met simulatie, robuustere eventstream; hashed lock-bestand, wekelijkse
audit, cosign-ondertekening. Databasemigraties 0002–0004 (§4).

## 2. Modules en diensten

### 2.1 Containers / processen (huidige installatiewijze: compose-stack, zie §11)

| Dienst | Image (tag, **geen digest**) | Rol | Bron |
|---|---|---|---|
| `db` | `postgres:16.15-alpine3.24` (uid 70) | alle gegevens incl. tijdreeksen en uurtotalen; LISTEN/NOTIFY | `infra/compose/compose.yaml:90-116` |
| `api` | `ghcr.io/lvos04/my_house-api:${SHC_VERSION:-latest}` (uid 10001) | FastAPI: login, rechten, validatie, beleidscontrole, overzichten; draait migraties bij start (met dump vooraf) | `compose.yaml:118-129`, `apps/api/Dockerfile` |
| `worker` | zelfde image, `command: worker` | enige die met apparaten praat: pollen/abonneren, commando's uitvoeren na 2e beleidscontrole, klimaatregelaar, waakhond, aggregaten, opruimen, klokcontrole, automatische back-ups (apart proces) | `compose.yaml:131-151`, `worker/core.py` |
| `web` | `ghcr.io/lvos04/my_house-web:${SHC_VERSION:-latest}` (nginx-unprivileged, uid 101) | PWA + reverse proxy `/api` en WebSocket; **enige gepubliceerde poort** `8088→8080`; inlogrem 10/min | `compose.yaml:153-174`, `apps/web/nginx.conf.template` |
| `mosquitto` | `eclipse-mosquitto:2.0.22` (uid 1883) | MQTT-broker, alleen intern netwerk `data` | `compose.yaml:176-190` |
| `zigbee2mqtt` (profiel `zigbee`) | `koenkk/zigbee2mqtt:2.14.3` (uid 1000, groep dialout) | Zigbee-coördinator via `/dev/serial/by-id/…` | `compose.yaml:195-219` |
| `cloudflared` (profiel `tunnel`) | `cloudflare/cloudflared:2026.10.7` | optionele tunnel; ziet alleen nginx; token als bestand | `compose.yaml:222-242` |

Netwerken: `edge` (cloudflared↔web), `app` (web↔api/worker, uitgang naar LAN-apparaten en NTP), `data` (intern, geen
route naar buiten) met vaste adressen als vertrouwenscontract (`compose.yaml:74-87`, `docs/architectuur.md` §Netwerken).
Elke container: `no-new-privileges`, `cap_drop: ALL`, read-only rootfs, geheugengrens (`compose.yaml:23-29`).

### 2.2 Code-modules

| Pad | Inhoud |
|---|---|
| `apps/api/src/shc_api/` | `main.py` (app, middlewares: beveiligingsheaders, audit, body-limiet), `deps.py` (principal, rollen), `policy.py` (commandobeleid), `commanding.py` (enige weg naar een commando), `climate.py` (pure regelaar), `energy.py` (tellers, kwaliteit), `adaptive.py`, `backup.py` (export/dump/herstel), `events.py` (LISTEN/NOTIFY → WebSocket), `watch.py` (worker-/back-upwacht), `notify.py` (push), `security.py` (wachtwoorden, tokens, AES-GCM-kluis), `store.py`, `views/` (lees-modellen), `routers/` (12 routers), `worker/` (core, commands, climate_loop, ingest, health, aggregates, backup_job), `cli.py`, `migrations/` |
| `integrations/src/shc_integrations/` | adapters `hue/`, `gree/` (eigen UDP-protocol, AES-ECB/GCM), `homewizard/` (v1/v2), `zigbee/` (MQTT), `base.py` (contract: capabilities, `CommandResult.confirmed`), `resilience.py` (breaker, retry, rate limit), `tls.py` (certificaatpinning), `registry.py` |
| `apps/web/` | React 18 + TypeScript + Vite PWA (**geen Next.js**), pagina's Overzicht, Klimaat, Verlichting, Energie, Optimalisatie, Apparaten, Systeem, Account, Installatie, Help (`src/App.tsx:45-54`); 164 vitest-tests, 10 Playwright-specs |
| `apps/android/` | Kotlin/Jetpack Compose-app (`app`, pure-Kotlin `core`), QR-koppeling, widget, meldingen |
| `infra/` | `compose/` (stack), `scripts/` (`install.sh`, `render_truenas.py`, `smoke_test.py`, `e2e_compose.sh`, `validate_compose.sh`), `e2e/` (browsertest tegen echte images + nep-Gree), `tests/` (13 unittests) |

### 2.3 Gegevens (tabellen, `models.py`)

`users`, `sessions`, `api_clients`, `pairing_codes`, `settings`, `audit_log`, `homes`, `rooms`, `integrations`,
`devices`, `logical_roles`, `device_bindings` (rol ↔ apparaat met geldigheid), `sensor_readings`, `comfort_profiles`,
`climate_zones`, `schedule_blocks`, `zone_log`, `adaptive_models`, `experiments`, `commands`, `button_mappings`,
`processed_events`, `tariffs`, `energy_hourly`, `alerts`, `backups`. Dit dekt het B06-datamodel (home, room, zone,
device, role_binding, measurement, energy_total, command, feedback (= command-status + zone_log), override (velden op
`climate_zones`), backup_manifest (`backups` + manifest in het bestand)).

## 3. API-routes

Prefix `/api/v1`. **116 HTTP-operaties op 91 paden + 1 WebSocket** in de OpenAPI (`docs/openapi/my_house.openapi.json`,
`info.version` 0.2.0), plus 2 verborgen beheerroutes. Toegangsniveaus (`docs/api.md:18-27`, `deps.py:131-176`):
`public` 8 · `auth` 33 · `control` 4 · `phone` 3 · `admin` 47 · `web-admin` 22. Fouten: HTTP-status +
`{"detail": {"code", "message"}}` (`docs/api.md:50-92`, `errors.py`).

| Bestand:regel | Methode | Pad | Toegang |
|---|---|---|---|
| main.py:251 | GET | `/openapi.json` (verborgen) | admin |
| main.py:255 | GET | `/docs` (verborgen) | admin |
| routers/auth.py:161 | GET | `/auth/status` | public |
| routers/auth.py:175 | POST | `/auth/setup` | public |
| routers/auth.py:193 | POST | `/auth/login` | public |
| routers/auth.py:217 | POST | `/auth/logout` | auth |
| routers/auth.py:232 | GET | `/auth/me` | auth |
| routers/auth.py:250 | POST | `/auth/password` | auth |
| routers/devices.py:183 | GET | `/homes/{home_id}/devices` | auth |
| routers/devices.py:192 | GET | `/devices/{device_id}` | auth |
| routers/devices.py:203 | PATCH | `/devices/{device_id}` | admin |
| routers/devices.py:228 | PATCH | `/devices/{device_id}/calibration` | admin |
| routers/devices.py:273 | POST | `/devices/{device_id}/bind` | admin |
| routers/devices.py:299 | POST | `/devices/{device_id}/unbind` | admin |
| routers/devices.py:350 | DELETE | `/devices/{device_id}` (archiveren) | admin |
| routers/devices.py:395 | POST | `/devices/{device_id}/replace` | admin |
| routers/devices.py:527 | POST | `/devices/{device_id}/test` (alleen lezen) | admin |
| routers/devices.py:649 | POST | `/devices/{device_id}/tested` | admin |
| routers/devices.py:680 | GET | `/devices/{device_id}/diagnostics` | admin |
| routers/devices.py:756 | GET | `/integrations` | auth |
| routers/devices.py:877 | GET | `/integrations/compatibility` | auth |
| routers/devices.py:944 | GET | `/integrations/{integration_id}` | admin |
| routers/devices.py:957 | GET | `/integrations/{provider}/instructions` | auth |
| routers/devices.py:1056 | POST | `/integrations/{provider}/discover` | admin |
| routers/devices.py:1230 | POST | `/integrations/{provider}/pair` | admin |
| routers/devices.py:1423 | PATCH | `/integrations/{integration_id}` (nieuw adres, `verify`) | admin |
| routers/devices.py:1481 | DELETE | `/integrations/{integration_id}` | admin |
| routers/devices.py:1500 | POST | `/integrations/zigbee/setup` | admin |
| routers/devices.py:1570 | POST | `/integrations/zigbee/permit-join` | admin |
| routers/energy.py:32 | GET | `/homes/{home_id}/energy/summary` | auth |
| routers/energy.py:45 | GET | `/homes/{home_id}/energy/live` | auth |
| routers/energy.py:71 | GET | `/homes/{home_id}/tariffs` | auth |
| routers/energy.py:152 | POST | `/homes/{home_id}/tariffs` | admin |
| routers/energy.py:169 | PUT | `/tariffs/{tariff_id}` | admin |
| routers/energy.py:192 | DELETE | `/tariffs/{tariff_id}` | admin |
| routers/energy.py:234 | GET | `/energy/limits` | auth |
| routers/energy.py:239 | PUT | `/energy/limits` | admin |
| routers/energy.py:265 | GET | `/homes/{home_id}/energy/export` | auth |
| routers/homes.py:47 | GET | `/homes` | auth |
| routers/homes.py:53 | POST | `/homes` | admin |
| routers/homes.py:75 | GET | `/homes/{home_id}/overview` | auth |
| routers/homes.py:100 | GET | `/homes/{home_id}/alerts` | auth |
| routers/homes.py:106 | POST | `/alerts/{alert_id}/ack` | control |
| routers/homes.py:119 | GET | `/homes/{home_id}/rooms` | auth |
| routers/homes.py:205 | GET | `/system/preflight` | admin |
| routers/lights.py:36 | GET | `/homes/{home_id}/lights` | auth |
| routers/lights.py:48 | POST | `/lights/groups/{group_id}` | control |
| routers/lights.py:165 | GET | `/homes/{home_id}/buttons` | auth |
| routers/lights.py:178 | GET | `/buttons/{device_id}` | auth |
| routers/lights.py:333 | PUT | `/buttons/{device_id}/mappings` | admin |
| routers/lights.py:378 | POST | `/buttons/{device_id}/mappings` | admin |
| routers/lights.py:391 | PATCH | `/button-mappings/{mapping_id}` | admin |
| routers/lights.py:403 | DELETE | `/button-mappings/{mapping_id}` | admin |
| routers/lights.py:462 | POST | `/buttons/{device_id}/simulate` (voert niets uit) | admin |
| routers/optimization.py:79 | GET | `/zones/{zone_id}/adaptive` | auth |
| routers/optimization.py:112 | POST | `/zones/{zone_id}/adaptive/fit` | admin |
| routers/optimization.py:140 | POST | `/zones/{zone_id}/adaptive/activate` | admin |
| routers/optimization.py:158 | POST | `/zones/{zone_id}/adaptive/disable` | admin |
| routers/optimization.py:225 | GET | `/homes/{home_id}/experiments` | auth |
| routers/optimization.py:233 | POST | `/homes/{home_id}/experiments` | admin |
| routers/optimization.py:267 | POST | `/experiments/{exp_id}/pause` | admin |
| routers/optimization.py:278 | POST | `/experiments/{exp_id}/resume` | admin |
| routers/optimization.py:313 | POST | `/experiments/{exp_id}/stop` | admin |
| routers/optimization.py:327 | DELETE | `/experiments/{exp_id}` | admin |
| routers/optimization.py:343 | GET | `/experiments/{exp_id}` | auth |
| routers/pairing.py:86 | GET | `/pairing/identity` | public |
| routers/pairing.py:117 | POST | `/pairing/codes` | web-admin |
| routers/pairing.py:197 | POST | `/pairing/redeem` | public |
| routers/pairing.py:248 | GET | `/clients` | web-admin |
| routers/pairing.py:259 | GET | `/clients/self` | phone |
| routers/pairing.py:267 | POST | `/clients/self/refresh` | phone |
| routers/pairing.py:287 | DELETE | `/clients/self` | phone |
| routers/pairing.py:301 | DELETE | `/clients/{client_id}` | web-admin |
| routers/profiles.py:82 | GET | `/homes/{home_id}/profiles` | auth |
| routers/profiles.py:91 | POST | `/homes/{home_id}/profiles` | admin |
| routers/profiles.py:110 | GET | `/profiles/{profile_id}` | auth |
| routers/profiles.py:115 | PUT | `/profiles/{profile_id}` | admin |
| routers/profiles.py:141 | DELETE | `/profiles/{profile_id}` | admin |
| routers/profiles.py:163 | POST | `/zones/{zone_id}/profile` | admin |
| routers/system.py:56 | GET | `/health` (controleert de database) | public |
| routers/system.py:62 | GET | `/system/status` | auth |
| routers/system.py:98 | GET | `/system/settings` | auth |
| routers/system.py:109 | PUT | `/system/settings` | web-admin |
| routers/system.py:118 | POST | `/system/safe-mode/release` | admin |
| routers/system.py:141 | GET | `/system/backups` | web-admin |
| routers/system.py:181 | POST | `/system/backups` | web-admin |
| routers/system.py:208 | GET | `/system/backups/{backup_id}/download` | web-admin |
| routers/system.py:304 | GET | `/system/backup-settings` | web-admin |
| routers/system.py:309 | PUT | `/system/backup-settings` | web-admin |
| routers/system.py:364 | GET | `/system/notify` | web-admin |
| routers/system.py:369 | PUT | `/system/notify` | web-admin |
| routers/system.py:377 | POST | `/system/notify/test` | web-admin |
| routers/system.py:445 | GET | `/system/retention` | admin |
| routers/system.py:450 | PUT | `/system/retention` | admin |
| routers/system.py:666 | POST | `/system/restore/verify` (wijzigt niets) | web-admin |
| routers/system.py:707 | DELETE | `/system/restore/uploads/{upload_id}` | web-admin |
| routers/system.py:753 | POST | `/system/restore/apply` | web-admin |
| routers/system.py:813 | GET | `/system/restore/jobs/{job_id}` (lang willekeurig id) | public |
| routers/system.py:823 | GET | `/system/restore/compare` | web-admin |
| routers/system.py:956 | POST | `/homes/{home_id}/archive` | admin |
| routers/users.py:63 | GET | `/users` | web-admin |
| routers/users.py:90 | POST | `/users` | web-admin |
| routers/users.py:95 | POST | `/auth/users` (verouderd pad) | web-admin |
| routers/users.py:130 | PATCH | `/users/{user_id}` | web-admin |
| routers/users.py:166 | POST | `/users/{user_id}/password` | web-admin |
| routers/users.py:182 | DELETE | `/users/{user_id}` | web-admin |
| routers/users.py:202 | GET | `/audit` | admin |
| routers/ws.py:89 | WS | `/ws?home_id=` (cookie + Origin, of eerste bericht `{"type":"auth","token":…}`) | eigen auth |
| routers/zones.py:39 | GET | `/homes/{home_id}/zones` | auth |
| routers/zones.py:48 | GET | `/zones/{zone_id}` | auth |
| routers/zones.py:61 | POST | `/zones/{zone_id}/command` | control |
| routers/zones.py:83 | POST | `/zones/{zone_id}/resume` | control |
| routers/zones.py:93 | GET | `/zones/{zone_id}/history` | auth |
| routers/zones.py:120 | GET | `/zones/{zone_id}/log` | auth |
| routers/zones.py:181 | GET | `/zones/{zone_id}/schedule` | auth |
| routers/zones.py:193 | PUT | `/zones/{zone_id}/schedule` | admin |
| routers/zones.py:248 | PUT | `/zones/{zone_id}/policy` | admin |
| routers/zones.py:275 | PATCH | `/zones/{zone_id}` | admin |
| routers/zones.py:301 | GET | `/homes/{home_id}/climate/roles` | auth |

Niet aanwezig (relevant voor het platformcontract): `/healthz` en `/readyz` op API-niveau (nginx heeft alleen een
eigen `location = /healthz`, `nginx.conf.template:110`), `/connector/v1/*`, `Idempotency-Key`, `ETag`,
cursor-paginering, een event-outbox. CLI (`cli.py:695-742`): `serve`, `worker`, `migrate`, `backup` (ook `--dump`),
`restore`, `verify-backup`, `restore-drill`, `rekey`, `reset-password`, `maintenance on|off|status`, `decrypt-dump`.

## 4. Database-migraties

Alembic, `apps/api/src/shc_api/migrations/versions/`; de API migreert bij start (`cli.py:141-157`) en maakt
eerst een `pg_dump` als de database een oudere revisie heeft (`pre_migration_dump`, contract C11) **[ROLLBACK]**.

| Revisie | Inhoud | Downgrade |
|---|---|---|
| `0001_initial_schema` | basisschema | ja |
| `0002_improvements` | inlogblokkade, telefoonrollen/-vervaldatum, stabiele integratie-identiteit + diagnose, kalibratie, comfortprofielen, commandovolging op zones, experimentautomatisering, Hue-knop-id's + dedupe, T1/T2-tarieven, `energy_hourly`, back-upmetadata, retentie-indexen | ja (verliest verwijderde dubbele tarieven) |
| `0003_followups` | `pairing_codes.role`, index `zone_log.at` | ja |
| `0004_legacy_phones` | oude telefoontokens krijgen vervaldatum en rol `control` (auditregel `legacy_phone_limited`) | ja |

Tests: `test_ws_backup_restore.py::test_older_backup_is_restored_in_its_schema_then_migrated_and_drops_are_listed`,
`::test_backup_of_newer_schema_is_refused`, `::test_pre_migration_dump_and_refusal_when_dump_fails`,
`test_gapfix_security.py::test_migration_0004_limits_legacy_phone_tokens`. **Ontbreekt:** een nette startweigering
als een oudere image een nieuwer schema aantreft (nu faalt `command.upgrade` en start de API niet — zie
DELTA_MATRIX §5, EX15).

## 5. Authenticatie, rollen en sleutels

- **Gebruikers** (`users`): rol `admin` of `viewer`; argon2id; eerste beheerder via `POST /auth/setup` (geen
  standaardwachtwoord); sessiecookie `shc_session` (HttpOnly, SameSite=Strict, `Secure` bij https) + CSRF-header
  `X-CSRF-Token` bij elke mutatie (`deps.py:116-125`); inlogrem per naam en per IP (`docs/api.md:100-108`).
- **Telefoons** (`api_clients`): via QR-koppelcode (`POST /pairing/codes`, 10 min, eenmalig) → `POST /pairing/redeem`
  → bearer-token `shc_…` met eigen rol `admin|control|viewer`, verloopt na 180 dagen, vernieuwen via
  `POST /clients/self/refresh`, intrekbaar; 401 `token_revoked`/`token_expired` (`deps.py:52-113`).
  Serverherkenning met HMAC-bewijs (`GET /pairing/identity`).
- **Toegangsniveaus** `public/auth/control/admin/web-admin/phone` (`deps.py:131-176`); `web-admin` weigert elke
  telefoon (403 `web_session_required`).
- **Geheimen:** hoofdsleutel `SHC_SECRET_KEY(_FILE)` (min. 32 tekens; `config.py:46-47`); apparaatsleutels en
  geheime instellingen (wachtwoordzin back-ups, push-adres) AES-GCM-versleuteld met een HKDF-afgeleide sleutel
  (`security.py:263-284`); `shc-api rekey` voor sleutelwissel. Database- en MQTT-wachtwoorden als bestanden uit
  `install.sh`.
- **Niet aanwezig:** machine-/dienstidentiteit voor de Hub (service-token, enrollment `POST {HUB_URL}/api/v1/services/enroll`),
  verificatie van Hub-JWT's (EdDSA/JWKS) of van een door de Hub ondertekend `ExecutionRequest`.

## 6. Events en notificaties

- **Realtime:** PostgreSQL LISTEN/NOTIFY (`events.py`, kanaal `shc_events`) → WebSocket `/api/v1/ws`. Berichttypen
  (`events.py:42-53`): `zone.updated`, `lights.updated`, `energy.live`, `alert.created`, `alert.resolved`,
  `status.updated`, `command.updated`, `command.queued`, `integration.changed`, `refresh`; intern `auth.revoked`.
  Geen replay/cursor: na herverbinden haalt de client het volledige overzicht opnieuw op.
- **Meldingen** (`alerts`): één open melding per oorzaak (`key`), automatisch opgelost als de oorzaak weg is
  (`store.raise_alert`/`resolve_alert`; `docs/api.md:215-221`).
- **Push buiten de app** (`notify.py`, `watch.py`): ntfy/webhook bij worker weg/terug en back-ups mislukt/te oud;
  adres versleuteld opgeslagen.
- **Logboek** (`audit_log`): elke geslaagde mutatie (`main.py` `AuditMiddleware`) + eigen domeinregels.
- **Niet aanwezig:** event-envelope §7, outbox met retry/dead-letter, push naar `POST {HUB_URL}/api/v1/events/inbox`,
  `GET /connector/v1/events?cursor=`.

## 7. Back-up, herstel en rollback **[ROLLBACK]**

- Volledige export `.shcbak` (formaat v2: scrypt + AES-256-GCM in STREAM-constructie, per-bestand sha256, manifest,
  ook Zigbee2MQTT-gegevens; `backup.py:1-28`, `write_backup` :808, `run_backup` :895) en versleutelde database-dump
  `.shcdump` (`run_db_dump` :1050). Automatisch met schema `daily_full` of `dump_daily_full_weekly`, inhalen na
  uitval, opruimen alleen van automatische back-ups (`worker/backup_job.py`; API `/system/backup-settings`).
- Herstel: `POST /system/restore/verify` (controleert alle checksums, **wijzigt niets** = dry-run), `apply` (één
  transactie; onderhoudsmodus aan tijdens herstel, **veilige modus** aan erna, `backup.py:1529`), `compare` (wat werkt
  per apparaat/functie). Oudere schema's worden teruggezet en daarna bijgewerkt, nieuwere geweigerd
  (`check_restorable` :1211). Modus `new_home` = verhuizen (oude woning gearchiveerd, profielen/schema's als concept).
- `shc-api restore-drill` (herstel in tijdelijke database, telt en vergelijkt), `verify-backup`, `decrypt-dump`.
- Pre-migratiedump bij elke schema-update (§4). Terugdraaien van een update staat in `docs/onderhoud.md:13-42`, maar
  vraagt `docker exec … psql` (terminal) — strijdig met contract 1.1 (§11, DELTA_MATRIX §5).

## 8. Monitoring en health **[MONITORING]**

- `GET /api/v1/health` (public, `SELECT 1`, `routers/system.py:56-59`); `GET /system/status` (database,
  achtergronddienst: hartslag en lussen, schijfruimte, back-up, tijdsynchronisatie, veilige modus, onderhoud,
  herstel, elke koppeling; `views/status.py:357`); `GET /system/preflight` (installatiewizard).
- Worker: hartslag elke 30 s (database + `/tmp/shc-worker-alive` voor de container-healthcheck), **waakhondthread**
  die het proces stopt bij een vastgelopen lus (`worker/core.py:368-422`), elke lus met backoff; koppeling pas op
  *fout* na 3 pogingen of 5 min.
- API-zijde `StatusWatch` (`watch.py`): melding *Achtergronddienst reageert niet* na 3 min, `status.updated`, push.
- Health-ronde per integratie, sensorveroudering (melding `stale:{device}`, `worker/health.py:231-263`),
  klokcontrole via SNTP.
- Container-healthchecks: db (`pg_isready`), api (`/api/v1/health`), worker (liveness-bestand), mosquitto, cloudflared.

## 9. Noodstop en veiligheidsinvarianten **[INTEGRITEIT]**

| Invariant | Waar afgedwongen | Test |
|---|---|---|
| **Boiler nooit schakelen** — beschermde rollen (`boiler`, `boiler_energy`) weigeren **elke** actie; meters (`p1_meter`, `kwh_meter`, `energy_socket`, `zigbee_sensor`) nooit aan/uit | (1) API: `policy.check_command` (`policy.py:37-54`) via `commanding.queue_command` (`commanding.py:84-90`) en `request_zone_command` (:154-163); (2) worker vlak voor verzenden (`worker/commands.py:140-146`); (3) adapter: HomeWizard `relay_allowed` standaard `False` (`homewizard/adapter.py:239-246, 500-506`), in worker en API vast op `False` (`worker/core.py:486`, `routers/devices.py:491`); UI biedt geen schakelknop | `test_api.py::test_boiler_socket_can_never_switch`, `test_core_logic.py::test_boiler_never_switchable[*]`, `::test_energy_socket_relay_blocked_even_without_role`, `integrations/tests/test_homewizard.py::test_v1_socket_poll_and_relay_blocked_by_default`, `::test_relay_allowed_only_via_policy_hook` |
| Boilerstekker uitgeschakeld of *Switch lock* uit → melding (geen schakelactie) | `worker/ingest.py:406-441` | `test_ws_devices_worker.py::test_protected_socket_relay_and_switch_lock_alerts` |
| **Gedeelde buitenunit:** geen verwarmen en koelen tegelijk (ook `auto`), onder advisory lock per buitenunit, opnieuw vlak voor verzenden | `climate.py:165-216`, `commanding.py:63-71, 175-177`, `worker/climate_loop.py:69-122` | `test_core_logic.py::test_shared_outdoor_conflict_blocks_cooling`, `test_api.py::test_zone_command_policy_and_execution` (409), `test_ws_climate_worker.py::test_two_zones_on_one_outdoor_unit_are_decided_together`, `::test_offline_and_auto_units_still_block_conflicting_modes`, `test_ws_climate_engine.py::test_auto_conflicts_with_heat_and_cool` |
| **Afstandsbediening wint:** wijziging = handmatige override; uit met afstandsbediening blijft uit tot *Terug naar schema* | `climate.py:297-310, 344-…` | `test_core_logic.py::test_remote_control_change_becomes_override`, `::test_remote_off_is_never_reactivated`, `test_ws_climate_engine.py::test_remote_change_during_off_becomes_manual`, `test_ws_climate_worker.py::test_remote_override_expires_back_to_schedule_without_loop` |
| **Verouderde/onwaarschijnlijke sensor** → geen bijsturing, OEM regelt; melding na `stale_after_s` | `climate.py:219-228, 492`; `worker/health.py:231-263` | `test_core_logic.py::test_stale_sensor_falls_back_to_oem_and_no_correction`, `::test_implausible_sensor`, `test_ws_climate_engine.py::test_invalid_sensor_resets_the_correction` (de melding zelf heeft **geen** eigen test) |
| Geen pendelen: min. interval, max. sprong, max. starts/uur | `climate.py` `DEFAULT_POLICY` (`:40-53`) | `test_core_logic.py::test_min_interval_respected`, `::test_max_starts_prevents_rapid_cycling` |
| **"Uitgevoerd" alleen na readback** (`confirmed`), anders `sent`→`failed` | `integrations/*/adapter.py` (`CommandResult.confirmed`), `worker/commands.py`, `docs/api.md:325-343` | `integrations/tests/test_gree.py::test_readback_mismatch_not_confirmed`, `test_hue.py::test_set_light_readback_mismatch_not_confirmed`, `test_hue_adapter.py::test_put_errors_mean_not_confirmed_even_if_readback_matches`, contract-kit, `test_ws_climate_worker.py::test_unconfirmed_command_is_confirmed_by_a_later_readback` |
| **Gree-compatibiliteitsgate:** zonder geslaagde scan + bind + statusuitlezing geen koppeling ("niet ondersteund", integratie uit, diagnose) | `gree/adapter.py:380-410`, `routers/devices.py:1100-1188` | `test_gree_bind.py::test_bind_failure_is_unsupported_with_scan_diagnostics`, `::test_status_failure_after_bind_is_unsupported`, `test_ws_devices_api.py::test_pair_failure_keeps_diagnostics_and_maps_errors` |
| **Veilige modus** na elk herstel: geen enkel commando tot een beheerder hem opheft; **onderhoudsmodus** tijdens herstel: worker pauzeert | `policy.py:39-43`, `backup.py:1529`, `worker/core.py:257-275`, `POST /system/safe-mode/release` | `test_api.py::test_backup_corruption_refused_and_restore_safe_mode`, `test_ws_devices_core.py::test_forever_pauses_while_maintenance` |
| Na herstart: niets sturen tot de stand van de unit is uitgelezen | `worker/climate_loop.py` | `test_core_logic.py::test_offline_unit_does_nothing` |
| Koppeladressen alleen in het thuisnetwerk, nooit de eigen netwerken | `routers/devices.py` host-validatie | `test_gapfix_operations.py::test_validate_host_refuses_the_stacks_own_subnets`, `test_ws_devices_api.py::test_pair_host_validation` |

**Noodstop:** er is geen aparte "alles stoppen"-knop. De veilige modus (aan na herstel) en de onderhoudsmodus (alleen
via herstel of `shc-api maintenance on`, CLI) houden alle commando's tegen. Een handmatige noodstop via UI/API
ontbreekt (DELTA_MATRIX §5).

## 10. CI, E2E en testbewijs

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | push (niet `main`), PR, `workflow_call` | `backend` (ruff, pytest integrations + api met postgres-service, pip-audit), `web` (tsc, vitest, build, npm audit), `e2e` (Playwright tegen PostgreSQL + API + gebouwde web-app, `apps/web/e2e/run-stack.sh`), `compose` (shellcheck, infra-unittests, `validate_compose.sh`), `stack` (images bouwen, Trivy, `install.sh`, compose-stack, rooktest, `verify-backup`, API-uitval, Playwright tegen de images met nep-Gree `infra/scripts/e2e_compose.sh`) |
| `release.yml` | push op `main` die `VERSION` wijzigt | CI + Android-tests/APK → images (SBOM, provenance, cosign) → GitHub-release |
| `edge.yml` | push op `main` | images `:edge` |
| `android.yml` | wijzigingen in `apps/android` | `:core:test`, `:app:testDebugUnitTest`, debug-APK |
| `audit.yml` | wekelijks (`23 4 * * 1`) | pip-audit, npm audit, Trivy op uitgebrachte images |

Ontbreekt t.o.v. contract §10: OpenAPI-export + driftcontrole, alembic up/down als aparte CI-stap, mypy, alles-in-één
image bouwen + starten met healthcheck, `release/digests.json`.

## 11. Images, tags en installatiewijze

- **Geen `deploy/`-map.** Installatie nu: `infra/scripts/install.sh` als **root via TrueNAS-shell** (mappen,
  eigenaars, geheimen) → `render_truenas.py` maakt één compose-YAML → **TrueNAS "Install via YAML"**
  (`docs/installatie-truenas.md:1-80`; `compose.yaml:6-7`). Dat is **strijdig met contract 1.1** (geen YAML, geen
  compose, geen terminal, één alles-in-één image). **De app is nu niet als standaard Custom App (één image) te
  installeren.**
- Images: `ghcr.io/lvos04/my_house-api` en `-web`; release pusht `:<versie>` **én `:latest`**
  (`.github/workflows/release.yml:101-103, 116-118`); compose valt terug op `${SHC_VERSION:-latest}`
  (`compose.yaml:56, 155`). Base-images alleen op tag, **niet op `@sha256`**: `python:3.12.16-slim-trixie`
  (`apps/api/Dockerfile:4, 39`), `postgres:16.15-trixie` (:37), `node:22.23.4-alpine3.24` (`apps/web/Dockerfile:3`),
  `nginxinc/nginx-unprivileged:1.30.6-alpine3.24` (:14); compose-images idem.
- Containers draaien als uid 10001/70/1883/101/1000 (contract: alles als 568 na eigenaarschapsstap).
- Geen manifest (`deploy/manifest/…`), geen `deploy/truenas/CUSTOM_APP.md`, geen `.env.example`.
- Wel: lock-bestand met hashes (`apps/api/requirements.txt`), SBOM + provenance + cosign bij release, Trivy-scan.

## 12. Releasebestanden

`VERSION` (0.2.0), `docs/release-notes.md` (0.2.0), `docs/testlog.md` (noemt nog "versie 0.1.0"), `SECURITY.md`,
`docs/verbeterlijst.md`. Geen `CHANGELOG.md`, geen `Makefile`, geen `release/digests.json`. Per abuis in de root
gecommit: `shellcheck_py-0.11.0.1-…whl` (3,8 MB binair).

## 13. Wat nooit dubbel gebouwd of uitgeschakeld mag worden (samenvatting)

1. **[INTEGRITEIT]** `policy.check_command` + de drie lagen boilerblokkade (API, worker, adapter) — nieuwe routes
   (connector) moeten dezelfde functie aanroepen, nooit een eigen beleid ernaast.
2. **[INTEGRITEIT]** `commanding.request_zone_command` / `queue_command` als **enige** weg naar een commando (incl.
   advisory lock per buitenunit, "kies eerst een modus", veilige modus). Een Hub-route roept deze aan; er komt geen
   tweede Gree-controller in de Hub (02_DATA_EIGENAARSCHAP).
3. **[INTEGRITEIT]** De klimaatregelaar (`climate.decide`) met override/afstandsbediening/sensorterugval — de Hub of
   AI mag geen alternatieve regelaar bouwen; AI mag hooguit een schema/profiel **voorstellen**.
4. **[INTEGRITEIT]** Readback-semantiek (`confirmed` pas na uitlezing) en de Gree-gate.
5. **[ROLLBACK]** Back-up/herstel/verhuizen (`backup.py`, restore-routes, `restore-drill`, pre-migratiedump,
   veilige modus na herstel). Het platform (B12) gebruikt dit via de eigenaar, bouwt geen eigen DB-export van my_house.
6. **[MONITORING]** `/health`, `/system/status`, worker-hartslag + waakhondthread, `StatusWatch` + push, sensor- en
   integratiehealth. Nieuwe `/readyz` en `/connector/v1/health` worden hieruit **afgeleid**.
7. Energie uit cumulatieve tellers met kwaliteitsvlaggen en rolbindingen met geldigheid — B14 (Hub) leest, rekent niet
   opnieuw.

## 14. Overige bevindingen (huishouding)

- Testdatabases heten `shc_t_*`, `shc_tmp_*`, `shc_drill_*` (prefix `shc` = deze repo; random suffix) en worden na
  afloop verwijderd (`apps/api/tests/conftest.py:8,30`).
- `PATCH /zones/{id}` accepteert als `unit_device_id` elk apparaat van de woning (`routers/zones.py:276-282`), ook een
  energiemeter; bediening daarvan wordt wel door `check_command` geweigerd (409 `policy_denied`), maar de validatie
  hoort `kind == "ac_unit"` te eisen.
- `POST /lights/groups/{boiler-id}` antwoordt 404 (geen lichtgroep), niet 409 `policy_denied` (`routers/lights.py:51`).
- Flaky: in de eerste volledige run hing `test_ws_devices_core.py::test_push_stream_batches_and_restarts` >11 min
  (§15); los en in de tweede run geslaagd.
- Playwright dekt 360/320 px en de standaard desktopbreedte; 390/768/1440 niet expliciet.
- Herstel na "wachtwoord vergeten" en sleutelwissel alleen via CLI (`reset-password`, `rekey`).

## 15. Baselinetestrun (2026-11-09, deze sessie)

Omgeving: Python 3.12.3 in een eigen venv; afhankelijkheden exact zoals CI uit `apps/api/requirements.txt`
(`pip install --require-hashes --only-binary=:all:` + `-e integrations[test] -e apps/api[dev]`, `pip check` schoon);
gedeelde PostgreSQL 16.15 op 127.0.0.1:5432 (`SHC_TEST_PG=postgresql://postgres@127.0.0.1:5432`); Node 22.

| Stap (zoals CI) | Commando | Uitkomst |
|---|---|---|
| Lint API | `ruff check apps/api/src apps/api/tests` | geslaagd ("All checks passed!") |
| Lint infra | `ruff check --config apps/api/pyproject.toml infra/scripts infra/e2e infra/tests` | geslaagd |
| Lint integrations | `ruff check integrations` (ruff 0.17.4 = CI-pin) | geslaagd |
| Adaptercontracttests | `pytest -q integrations/tests` | **188 geslaagd, 1 overgeslagen** (20 s). Overgeslagen: `contract_kit.py:179` "geen readback om te vervalsen" (contracttest die voor een adapter zonder readback niet van toepassing is) |
| API, engine, migraties — run 1 | `pytest -q -rs tests` (in `apps/api`) | **afgebroken**: 187 geslaagd, daarna hing test 188 `test_ws_devices_core.py::test_push_stream_batches_and_restarts` > 11 min (DB-verbindingen idle, proces ~6 % CPU); handmatig gestopt, testdatabase opgeruimd |
| — losse herhaling | `pytest -q tests/test_ws_devices_core.py` | **9/9 geslaagd** (3,4 s) |
| API, engine, migraties — run 2 | `pytest -rs -v --durations=15 tests` | **319/319 geslaagd** in 88 s, 0 overgeslagen |
| Infra-scripts | `python3 -m unittest discover -s infra/tests` | **13/13 geslaagd** |
| Compose/TrueNAS-render | `bash infra/scripts/validate_compose.sh` | geslaagd (alle profielen en renders `ok`) |
| Shellcheck | `shellcheck infra/scripts/*.sh` | **niet gedraaid** (shellcheck niet geïnstalleerd; de gecommitte wheel is niet gebruikt) |
| Web | `npm ci` · `npx tsc --noEmit` · `npx vitest run --minWorkers=1 --maxWorkers=2` | geslaagd · geslaagd · **164/164 geslaagd** in 23 bestanden (eerste poging met alleen `--maxWorkers=2` startte niet: tinypool-fout min/maxThreads, geen testfout) |
| Browsertests | `apps/web/e2e/run-stack.sh`, `infra/scripts/e2e_compose.sh` | **niet gedraaid** in deze sessie (zwaar; gedeelde machine). Wel geslaagd in CI-run 42587407462 (jobs `e2e` en `images, scan and smoke test`) op `9c9d02f` |
| Android | `./gradlew :core:test :app:testDebugUnitTest` | **niet gedraaid** (geen Android SDK hier); laatste Android-runs op Dependabot-branches mislukt, release-run 0.2.0 mislukt op Gradle-download (§1) |

Conclusie baseline: de bestaande backend-, adapter-, infra- en webtests slagen in deze omgeving; één test is
**flaky** (kan hangen). Niets hiervan is een validatie op echte hardware of op de NAS.
