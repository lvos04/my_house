# API-contract v1

Basis-URL: `/api/v1`. JSON overal. Tijden zijn ISO-8601 in UTC (`2026-11-08T13:40:00Z`); de client toont ze in de tijdzone van de woning (`Home.timezone`).

## Authenticatie

Twee manieren, beide verplicht voor alle routes behalve `GET /auth/status`, `POST /auth/setup`, `POST /auth/login`, `POST /pairing/redeem` en `GET /health`:

1. **Web (cookie)** — `POST /auth/login` zet `shc_session` (HttpOnly, SameSite=Strict, Secure achter HTTPS) en `shc_csrf` (leesbaar). Elke muterende request (POST/PUT/PATCH/DELETE) met cookie-auth stuurt header `X-CSRF-Token: <waarde van shc_csrf>`.
2. **App (bearer)** — `Authorization: Bearer shc_<token>`. Verkregen via QR-koppeling. Geen CSRF nodig. Intrekbaar.

Achter Cloudflare Access kan de Android-app extra headers `CF-Access-Client-Id` / `CF-Access-Client-Secret` meesturen (service token); de API negeert die.

Fouten: HTTP-status + `{"detail": {"code": "string", "message": "Nederlandse uitleg"}}`.
Codes o.a. `auth_required`, `csrf_failed`, `forbidden`, `not_found`, `policy_denied`, `rate_limited`, `device_offline`, `unsupported`, `validation_error`, `setup_done`.

Rollen: `admin` (alles), `viewer` (alleen lezen).

| Methode | Pad | Body | Antwoord |
|---|---|---|---|
| GET | `/auth/status` | – | `{setup_required: bool, user: User\|null}` |
| POST | `/auth/setup` | `{username, password}` (min. 12 tekens) | `{user, csrf_token}` + cookies. 409 `setup_done` als er al gebruikers zijn |
| POST | `/auth/login` | `{username, password}` | `{user, csrf_token}` + cookies. 429 bij te veel pogingen |
| POST | `/auth/logout` | – | 204 |
| GET | `/auth/me` | – | `User` |

`User = {id, username, role: "admin"|"viewer"}`

## Koppelen van een telefoon (QR)

| Methode | Pad | Body | Antwoord |
|---|---|---|---|
| POST | `/pairing/codes` (admin) | `{label?: string}` | `{code, expires_at, qr_payload}` |
| POST | `/pairing/redeem` | `{code, device_name}` | `{token, user, server: {lan_url, remote_url, home_id, home_name}}` |
| GET | `/clients` (admin) | – | `[{id, name, created_at, last_used_at, revoked}]` |
| DELETE | `/clients/{id}` (admin) | – | 204 |

`qr_payload` is een JSON-string (dit staat in de QR-code):
`{"v":1,"type":"shc-pair","code":"K7Q4-9XPM","lan":"http://192.168.1.20:8080","remote":"https://huis.voorbeeld.nl","home":"Thuis"}`
`remote` is `null` als er geen tunnel is ingesteld. Code is 10 minuten geldig en eenmalig.

## Woningen en overzicht

| Methode | Pad | Antwoord |
|---|---|---|
| GET | `/homes` | `[Home]` |
| POST | `/homes` (admin) | `Home` |
| GET | `/homes/{home_id}/overview` | `Overview` |

```
Home = {id, name, timezone, currency, active: bool}
Overview = {
  home: Home,
  status: [{key, label, state: "ok"|"warn"|"error"|"unknown", detail}],
  zones: [Zone],
  lights: [LightGroup],
  energy_today: EnergySummary,
  power_now: LivePower|null,
  alerts: [Alert]        // open alerts, nieuwste eerst
}
Alert = {id, severity: "info"|"warn"|"error", title, detail, created_at, acknowledged: bool, source}
```

## Klimaat

```
Measurement = {value: number|null, unit, quality: "measured"|"derived"|"estimated"|"missing", at: ts|null, stale: bool}
Zone = {
  id, name, room,
  online: bool,
  mode: "off"|"heat"|"cool"|"auto"|"dry"|"fan",
  target_c: number|null,
  fan: "auto"|"low"|"medium"|"high"|null,
  temperature: Measurement,       // externe sensor of unit
  humidity: Measurement,
  control: "schedule"|"manual"|"oem_fallback"|"off",
  profile: string|null,
  override: {until: ts|null, source: "app"|"remote_control"|"user"}|null,
  capabilities: {modes: [..], fan: bool, swing: bool, min_c, max_c, step_c},
  blocked_modes: [{mode, reason}],  // b.v. gedeelde buitenunit
  next_change: {at, target_c, label}|null,
  policy: {band_c, min_interval_s, max_step_c, max_starts_per_hour, stale_after_s},
  notice: string|null              // korte uitleg om te tonen (b.v. sensor verouderd)
}
```

| Methode | Pad | Body | Antwoord |
|---|---|---|---|
| GET | `/homes/{home_id}/zones` | – | `[Zone]` |
| GET | `/zones/{id}` | – | `Zone` |
| POST | `/zones/{id}/command` | `{mode?, target_c?, fan?, override_minutes?}` | 202 `{command_id, status: "queued"}` of 409 `policy_denied` / 503 `device_offline` |
| POST | `/zones/{id}/resume` | – | `Zone` (handmatige override weg, terug naar schema) |
| GET | `/zones/{id}/history?hours=24` | – | `{temperature: [[ts, v]], target: [[ts, v]]}` |
| GET | `/zones/{id}/log?limit=50` | – | `[{at, kind, message}]` |
| GET/PUT | `/zones/{id}/schedule` | `{blocks: [{days:[0..6], start:"HH:MM", target_c, label}]}` | idem |
| PUT | `/zones/{id}/policy` (admin) | policy-object | `Zone` |

Een commando is pas "uitgevoerd" als de readback van het apparaat het bevestigt; tot die tijd toont de UI "verzonden".

## Verlichting (Hue)

```
LightGroup = {id, name, on: bool, brightness: 0-100|null, scene: string|null, reachable: bool, lights_on: int, lights_total: int}
Scene = {id, name, group_id}
Button = {id, name, model, events: [string], mapping: [ButtonMapping]}
ButtonMapping = {id, event, mode: "lights"|"climate"|"combined", action: {...}, conflict: string|null}
```

| Methode | Pad | Body |
|---|---|---|
| GET | `/homes/{home_id}/lights` | → `{groups: [LightGroup], scenes: [Scene]}` |
| POST | `/lights/groups/{id}` | `{on?, brightness?, scene_id?}` → 202 |
| GET | `/homes/{home_id}/buttons` | → `[Button]` |
| PUT | `/buttons/{id}/mappings` (admin) | `[ButtonMapping]` → 200 of 409 met conflicten |

## Energie

```
EnergySummary = {
  period: "day"|"week"|"month"|"year", start, end,
  total_kwh, cost: number|null, currency,
  meters: [{role, label, kwh, cost, quality, note}],   // role: grid_import, grid_export, airco, boiler, rest
  buckets: [{start, values: {role: kwh}, complete: bool}]
}
LivePower = {at, total_w, parts: [{role, label, watts, quality}]}
Tariff = {id, valid_from, import_per_kwh, export_per_kwh, fixed_per_day, source}
```

| Methode | Pad |
|---|---|
| GET | `/homes/{home_id}/energy/summary?period=day&date=2026-11-08` |
| GET | `/homes/{home_id}/energy/live` |
| GET/POST | `/homes/{home_id}/tariffs` |
| GET | `/homes/{home_id}/energy/export?format=csv\|json&from=&to=` |

"rest" heeft altijd `quality: "derived"`. Per binnenunit bestaat geen meter-rol.

## Apparaten en integraties

```
Device = {id, provider, kind, name, stable_identity, room, roles: [string], online, last_seen, capabilities: [string], protected: bool, compatibility: "tested"|"untested"|"unsupported"}
Integration = {provider, label, state: "ok"|"warn"|"error"|"not_configured", detail, last_ok_at}
```

| Methode | Pad |
|---|---|
| GET | `/homes/{home_id}/devices` |
| GET | `/integrations` |
| POST | `/integrations/{provider}/discover` (admin) → `[DiscoveredDevice]` |
| POST | `/integrations/{provider}/pair` (admin) `{home_id, host, name?, options?}` → `Device`, of 202 `{status:"action_required", steps, retry_after_s}` |
| GET | `/integrations/{provider}/instructions` → stappen bij de fabrikant |
| POST | `/integrations/zigbee/setup` (admin) `{home_id}` → `Device` (kind `zigbee_bridge`) |
| POST | `/integrations/zigbee/permit-join` (admin) `{home_id, seconds}` → 202 `{command_id, open_until}` |
| PATCH | `/devices/{id}` (admin) `{name?, room_id?}` |
| POST | `/devices/{id}/bind` (admin) `{role}` — vorige binding wordt gesloten, historie blijft |

## Systeem

| Methode | Pad |
|---|---|
| GET | `/health` (zonder auth) → `{status, version}` |
| GET | `/system/status` → `{checks: [{key, label, state, detail}]}` |
| GET/POST | `/system/backups` (admin) — POST `{passphrase (≥12), include_secrets}` |
| GET | `/system/backups/{id}/download` (admin) |
| GET/PUT | `/system/settings` `{lan_url, remote_url}` |
| POST | `/system/safe-mode/release` (admin) |
| POST | `/homes/{id}/archive` (admin) — verhuisd: woning alleen-lezen, historie blijft |
| GET | `/homes/{home_id}/alerts` · POST `/alerts/{id}/ack` |

## Realtime (WebSocket)

`GET /api/v1/ws?home_id=<id>` — cookie-auth of eerste bericht `{"type":"auth","token":"shc_..."}`.
Server stuurt `{"type": "...", "data": {...}}` met types `zone.updated` (Zone), `lights.updated` (LightGroup), `energy.live` (LivePower), `alert.created` (Alert), `status.updated` (Overview.status). Client reconnect met backoff; na reconnect volledige `overview` opnieuw ophalen.
