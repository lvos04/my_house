# API-contract v1

Basis-URL: `/api/v1`. JSON overal, behalve de energie-export (CSV of JSON als download), het downloaden
van een back-up (binair) en het uploaden van een back-up (multipart of binair). Tijden zijn ISO-8601 in
UTC (`2026-11-08T13:40:00Z`); de client toont ze in de tijdzone van de woning (`Home.timezone`). Id's
zijn UUID's, tenzij anders vermeld.

De volledige, actuele lijst (OpenAPI) staat op `GET /api/v1/docs` en `GET /api/v1/openapi.json`. Die
zijn alleen te zien voor een ingelogde beheerder, dus nooit publiek via de tunnel.

## Toegang en rollen

**Gebruikers** hebben de rol `admin` (beheerder) of `viewer` (alleen kijken). **Telefoons** worden
door een beheerder gekoppeld en krijgen daarbij een eigen rol: `control` (bedienen, standaard),
`viewer` (alleen kijken) of `admin` (beheren). Een telefoon handelt namens de beheerder die hem
koppelde, maar nooit met meer rechten dan zijn eigen rol.

Elke route heeft één van deze toegangsniveaus (kolom *Toegang* in de tabellen hieronder):

| Toegang | Wie mag het |
|---|---|
| `public` | Iedereen, zonder login. |
| `auth` | Elke ingelogde gebruiker en elke gekoppelde telefoon, ook `viewer`. |
| `control` | Bedienen (klimaat, licht, meldingen afhandelen): een `admin` via de web-app, of een telefoon met rol `admin` of `control`. Anders 403 `forbidden`. |
| `admin` | Beheren (apparaten, zones, schema's, profielen, tarieven, bewaartermijnen, veilige modus, logboek): een `admin` via de web-app, of een telefoon met rol `admin`. Anders 403 `forbidden`. |
| `web-admin` | Alleen een `admin` in de web-app, ingelogd met wachtwoord: gebruikers, telefoons en koppelcodes, back-ups en herstellen, adressen en het push-adres. Een telefoon krijgt hier altijd 403 `web_session_required`, ook met rol `admin`. Zo kan iemand met een gestolen telefoon geen nieuwe beheerder, koppelcode of back-up maken. |
| `phone` | Alleen een gekoppelde telefoon zelf (bearer-token). Anders 403 `forbidden`. |

`GET /auth/me` vertelt de client wat hij mag: `is_admin` (niveau `admin`) en `can_control`
(niveau `control`).

## Authenticatie

1. **Web (cookie).** `POST /auth/login` zet `shc_session` (HttpOnly, SameSite=Strict, `Secure` alleen
   als het verzoek via https binnenkwam, dus via de tunnel) en `shc_csrf` (leesbaar voor JavaScript).
   Elke muterende request (POST/PUT/PATCH/DELETE) met cookie stuurt de header
   `X-CSRF-Token: <waarde van shc_csrf>`, anders 403 `csrf_failed`. Een sessie is 14 dagen geldig.
2. **Telefoon (bearer).** `Authorization: Bearer shc_<token>`, verkregen via QR-koppeling. Geen CSRF
   nodig. Het token verloopt na **180 dagen**; de app vernieuwt het met `POST /clients/self/refresh`.
   Een ingetrokken token geeft 401 `token_revoked`, een verlopen token 401 `token_expired` (de app
   toont dan "koppel opnieuw" in plaats van "geen verbinding").

Achter Cloudflare Access stuurt de Android-app extra headers `CF-Access-Client-Id` /
`CF-Access-Client-Secret` mee (service token), alleen naar het externe adres. De API negeert die.

Uitloggen, een wachtwoordwijziging, een uitgeschakelde of verwijderde gebruiker en een ingetrokken
telefoon sluiten ook de open WebSocket-verbindingen van die sessie of dat token (zie
[Realtime](#realtime-websocket)).

## Fouten

HTTP-status + `{"detail": {"code": "string", "message": "Nederlandse uitleg"}}`. Validatiefouten
(422) hebben daarnaast `errors: [{loc, msg, type}]`; een conflict bij knopkoppelingen (409) heeft
`conflicts: [{index, message}]`; een mislukte koppeling (502/504/409) heeft `integration_id`,
`device_id` en `diagnostics`. Bij 429 staat er een `Retry-After`-header bij.

| Status | Code | Betekenis |
|---|---|---|
| 400 | `invalid_code` | Koppelcode ongeldig, verlopen of al gebruikt |
| 401 | `auth_required` | Niet (meer) ingelogd, of gebruikersnaam/wachtwoord fout |
| 401 | `token_revoked` | Telefoon-token ingetrokken (door beheerder, "Ontkoppelen" of wachtwoordwijziging) |
| 401 | `token_expired` | Telefoon-token verlopen (180 dagen niet vernieuwd) |
| 403 | `csrf_failed` | CSRF-header ontbreekt of klopt niet |
| 403 | `forbidden` | Rol of toegangsniveau te laag (bijvoorbeeld `viewer` die wil bedienen) |
| 403 | `web_session_required` | Alleen in de web-app met wachtwoord, niet vanaf een telefoon |
| 403 | `password_required` | Telefoon koppelen met rol `admin` vraagt je eigen wachtwoord |
| 403 | `wrong_password` | Opgegeven (huidig) wachtwoord klopt niet |
| 404 | `not_found` | Bestaat niet |
| 409 | `setup_done` | Er is al een beheerder |
| 409 | `username_taken`, `last_admin`, `self_change`, `self_delete` | Gebruikersbeheer: naam bestaat al, laatste actieve beheerder, eigen rol/account |
| 409 | `lan_url_required`, `lan_url_invalid`, `remote_url_invalid` | Eerst een geldig thuisadres (en tunneladres) instellen |
| 409 | `policy_denied` | Veiligheidsbeleid weigert (boiler, gedeelde buitenunit, veilige modus, model te onbetrouwbaar) |
| 409 | `mode_required` | Airco staat uit: kies eerst een modus voordat je temperatuur of ventilator wijzigt |
| 409 | `unsupported` | Kan dit apparaat niet (geen airco gekoppeld, geen ventilator, niet te kalibreren) |
| 409 | `role_mismatch`, `not_bound`, `archived` | Rol past niet bij het apparaat, apparaat heeft die rol niet, apparaat of woning gearchiveerd |
| 409 | `identity_mismatch`, `certificate_mismatch` | Op dat adres antwoordt een ander apparaat / ander certificaat |
| 409 | `conflict` | Conflict (dubbele profielnaam, knopkoppeling, experimentstatus) |
| 409 | `tariff_exists` | Er is al een tarief met die ingangsdatum |
| 409 | `insufficient_data` | Te weinig metingen voor een adaptief model |
| 409 | `not_configured` | Eerst instellen (Zigbee, push-adres) |
| 409 | `backup_running`, `restore_running`, `restore_refused` | Herstel kan nu niet: back-up of herstel loopt, of back-up niet herstelbaar |
| 413 | `payload_too_large` | Verzoek te groot (zie [Limieten](#limieten)); `request_too_large` als nginx weigert |
| 422 | `validation_error` | Ongeldige invoer |
| 422 | `range_too_large` | Ruwe export: maximaal 31 dagen per keer |
| 422 | `backup_invalid` | Back-up beschadigd, verkeerde wachtwoordzin of geen back-up |
| 429 | `rate_limited` | Te veel pogingen of server even druk |
| 500 | `backup_failed` | Back-up maken mislukt |
| 502 | `device_offline`, `pair_failed`, `pairing_required`, `discovery_failed`, `adapter_error` | Apparaat niet bereikbaar of gaf een fout bij zoeken/koppelen |
| 503 | `device_offline` | Airco niet bereikbaar (commando niet in de wachtrij gezet) |
| 503 | `api_unavailable` | Van nginx: de API start op of wordt bijgewerkt |
| 503 | `secret_key_invalid` | De sleutel van deze server (`SHC_SECRET_KEY`) ontbreekt of is ongeldig: back-up niet te controleren, versleutelde instelling of koppeling niet te lezen of op te slaan |
| 504 | `device_offline` | Apparaat antwoordt niet binnen de tijd |
| 507 | `disk_full` | Te weinig schijfruimte voor de upload |

## Limieten

- **Grootte van een verzoek:** maximaal 256 kB (`SHC_MAX_BODY_BYTES`), anders 413
  `payload_too_large`. Uploads onder `/system/restore` mogen tot 2 GB (`SHC_MAX_UPLOAD_BYTES`); ze
  worden direct naar schijf gestreamd. De API controleert de werkelijk ontvangen bytes, niet alleen
  `Content-Length`.
- **Inloggen:** na 5 mislukte pogingen op één gebruikersnaam is die naam 1 minuut geblokkeerd, daarna
  bij elke volgende fout dubbel zo lang, tot maximaal 15 minuten. Per IP-adres maximaal 20 mislukte
  pogingen per 5 minuten. Beide geven 429 `rate_limited` met `Retry-After`. Een beheerder kan een
  blokkade opheffen (`PATCH /users/{id}` met `unlock`).
- **Koppelcode inwisselen:** maximaal 10 mislukte pogingen per IP-adres per 5 minuten.
- **nginx** (voor de API): `auth/login`, `auth/setup` en `pairing/redeem` samen maximaal 10 verzoeken per
  minuut per IP-adres (piek 5), daarna 429.
- Het IP-adres is het echte adres van de bezoeker: de API gelooft `X-Forwarded-For` alleen van nginx,
  en nginx gelooft het adres van Cloudflare alleen van de tunnelcontainer.

## Inloggen en eigen account

| Methode | Pad | Toegang | Body | Antwoord |
|---|---|---|---|---|
| GET | `/auth/status` | `public` | – | `{setup_required: bool, user: User\|null}` |
| POST | `/auth/setup` | `public` | `{username, password}` (min. 12 tekens) | `{user, csrf_token}` + cookies. 409 `setup_done` als er al gebruikers zijn |
| POST | `/auth/login` | `public` | `{username, password}` | `{user, csrf_token}` + cookies. 401 `auth_required`, 429 `rate_limited` |
| POST | `/auth/logout` | `auth` | – | 204 |
| GET | `/auth/me` | `auth` | – | `User` + `{via: "cookie"\|"token", client_role, is_admin, can_control, password_changed_at}` |
| POST | `/auth/password` | `auth` | `{old_password, new_password, keep_phones?: false}` | `{sessions_ended, phones_revoked}`. Je andere web-sessies worden uitgelogd en, tenzij `keep_phones`, je telefoons ingetrokken. De sessie of telefoon die de wijziging doet, blijft ingelogd. 403 `wrong_password` |

`User = {id, username, role: "admin"|"viewer"}`. Gebruikersnaam: 2–64 tekens `A-Z a-z 0-9 _ . - @`.

## Gebruikers en logboek

| Methode | Pad | Toegang | Body | Antwoord |
|---|---|---|---|---|
| GET | `/users` | `web-admin` | – | `[UserFull]` |
| POST | `/users` | `web-admin` | `{username, password, role}` | `UserFull`. 409 `username_taken` |
| POST | `/auth/users` | `web-admin` | idem | Verouderd pad voor `POST /users` |
| PATCH | `/users/{id}` | `web-admin` | `{role?, disabled?, unlock?}` | `UserFull`. Uitschakelen logt alle sessies uit en trekt telefoons in. 409 `self_change`, `last_admin` |
| POST | `/users/{id}/password` | `web-admin` | `{password}` | `{sessions_ended, phones_revoked}`: nieuw wachtwoord voor een ander; diens sessies en telefoons vervallen |
| DELETE | `/users/{id}` | `web-admin` | – | 204. 409 `self_delete`, `last_admin` |
| GET | `/audit?limit=100&before=&actor=&action=` | `admin` | – | `[{id, at, actor, action, target, detail}]`, nieuwste eerst; verder bladeren met `before=<id van de laatste regel>` (`limit` 1–500) |

```
UserFull = {id, username, role, disabled, created_at, password_changed_at, failed_logins,
            locked_until, locked: bool, active_sessions, active_phones}
```

Het logboek bevat een regel voor elk geslaagd muterend verzoek (`action: "request"`, met methode,
route-sjabloon en status; nooit de body) plus eigen regels voor gebeurtenissen zoals `login`,
`login_failed`, `user_locked`, `phone_paired`, `phone_revoked`, `backup_created`, `restore_started`
en `legacy_phone_limited` (database-update 0004). Wachtwoorden en sleutels komen er nooit in.

## Telefoons koppelen (QR)

| Methode | Pad | Toegang | Body | Antwoord |
|---|---|---|---|---|
| POST | `/pairing/codes` | `web-admin` | `{label?, role?: "control"\|"viewer"\|"admin", password?}` | `{code, expires_at, role, server_id, qr_payload}`. Rol `admin` vraagt `password` (je eigen wachtwoord): 403 `password_required` / `wrong_password`. 409 `lan_url_required` als er nog geen thuisadres is |
| POST | `/pairing/redeem` | `public` | `{code, device_name}` | `{token, user, client: Client, server: {server_id, lan_url, remote_url, home_id, home_name}}`. 400 `invalid_code` |
| GET | `/pairing/identity?nonce=&client_id=` | `public` | – | `{server_id, name, proof?, proof_alg?}` (zie hieronder) |
| GET | `/clients` | `web-admin` | – | `[Client + {user}]` |
| DELETE | `/clients/{id}` | `web-admin` | – | 204: telefoon intrekken |
| GET | `/clients/self` | `phone` | – | `Client + {user}` |
| POST | `/clients/self/refresh` | `phone` | – | `{token, expires_at}`: nieuw token, weer 180 dagen geldig. Het oude token werkt meteen niet meer |
| DELETE | `/clients/self` | `phone` | – | 204: "Ontkoppelen" in de app; het token werkt ook op de server niet meer |

```
Client = {id, name, role: "admin"|"control"|"viewer", created_at, last_used_at,
          expires_at, refreshed_at, revoked: bool, expired: bool}
```

`qr_payload` is een JSON-string (dit staat in de QR-code):
`{"v":1,"type":"shc-pair","code":"K7Q4-9XPM","lan":"http://192.168.1.20:8088","remote":"https://huis.voorbeeld.nl","home":"Thuis","server_id":"…","role":"control"}`.
`remote` is `null` als er geen tunnel is ingesteld. De code is 10 minuten geldig en werkt één keer.
De adressen komen uit **Systeem › Adressen** (of `SHC_LAN_URL` / `SHC_REMOTE_URL`), nooit uit het
binnenkomende verzoek. Het thuisadres moet een adres in het thuisnetwerk zijn (http mag); het externe
adres moet https zijn.

**Serverherkenning.** Voordat de app zijn token naar een adres stuurt, vraagt hij
`GET /pairing/identity` met een nieuw willekeurig `nonce` (16–128 tekens `A-Z a-z 0-9 _ -`) en zijn
eigen `client_id`. De server antwoordt `{server_id, name, proof, proof_alg: "hmac-sha256-v1"}` met

```
proof = hex(HMAC-SHA256(key = UTF-8 van sha256(token) als kleine-letter-hex,
                        msg = "shc-identity-v1\n" + server_id + "\n" + nonce))
```

Alleen deze server (die de hash van het token bewaart) en de telefoon (die het token heeft) kunnen dit
berekenen; het token zelf gaat niet over de lijn. Voor een onbekende `client_id` is `proof` `null`.
Zonder `nonce` en `client_id` antwoordt de server alleen `{server_id, name}` (gebruikt bij het
koppelen, waar de app de `server_id` uit de QR-code vergelijkt).

Telefoons die vóór database-update 0002 zijn gekoppeld en nooit een vervaldatum kregen, vervallen
sinds update 0004 180 dagen na hun laatste gebruik (minstens 30 dagen na de update) en hebben rol
`control`. Wil je zo'n telefoon weer laten beheren, koppel hem dan opnieuw met rol `admin`.

## Woningen, overzicht en meldingen

| Methode | Pad | Toegang | Body | Antwoord |
|---|---|---|---|---|
| GET | `/homes` | `auth` | – | `[Home]` |
| POST | `/homes` | `admin` | `{name, timezone?: "Europe/Amsterdam", currency?: "EUR"}` | `Home`, met standaardrollen, kamers Woonkamer en Slaapkamer en twee klimaatzones |
| GET | `/homes/{home_id}/overview` | `auth` | – | `Overview` |
| GET | `/homes/{home_id}/rooms` | `auth` | – | `[{id, name, function}]` |
| GET | `/homes/{home_id}/alerts` | `auth` | – | `[Alert]`, open meldingen (max. 100), nieuwste eerst |
| POST | `/alerts/{id}/ack` | `control` | – | `Alert`: als gezien markeren. De melding blijft open tot de oorzaak weg is |
| POST | `/homes/{id}/archive` | `admin` | – | `Home`: verhuisd. Woning wordt inactief, historie blijft, koppelingen gaan uit |

```
Home = {id, name, timezone, currency, active: bool}
Overview = {
  home: Home,
  status: [Check],            // achtergronddienst, back-up, veilige modus, onderhoud, herstel, koppelingen
  zones: [Zone],
  lights: [LightGroup],
  energy_today: EnergySummary,
  power_now: LivePower|null,
  alerts: [Alert]             // open meldingen, nieuwste eerst
}
Check = {key, label, state: "ok"|"warn"|"error"|"unknown", detail, ...}
Alert = {id, key, severity: "info"|"warn"|"error", title, detail, created_at, acknowledged: bool, source}
```

Een melding wordt één keer geopend per oorzaak en verdwijnt (`alert.resolved`) zodra de oorzaak weg is.
`key` is die oorzaak, vast en machineleesbaar, bijvoorbeeld `watchdog:{zone_id}` (kamer warmt of koelt
niet), `backup_failed` of `integration:{id}:{code}`. Er is per `key` hoogstens één open melding. Doet de
oorzaak zich opnieuw voor terwijl de melding nog open is, dan houdt hij zijn `id` en krijgt hij de nieuwe
`title` en `detail` (bijvoorbeeld een andere zonenaam of een nieuwe tijd voor de volgende poging).
Het overzicht controleert ook zelf de hartslag van de achtergronddienst: is die ouder dan 3 minuten,
dan staat er een melding *Achtergronddienst reageert niet*.

## Klimaat

```
Measurement = {value: number|null, unit, quality: "measured"|"derived"|"estimated"|"missing", at: ts|null, stale: bool}
Zone = {
  id, name, room,
  unit_device_id, sensor_device_id,
  unit_linked: bool, unit_status: "not_linked"|"online"|"offline", online: bool,
  mode: "off"|"heat"|"cool"|"auto"|"dry"|"fan",
  target_c: number|null,
  fan: string|null,
  temperature: Measurement,          // externe sensor, anders de eigen sensor van de unit
  humidity: Measurement,
  control: "schedule"|"manual"|"oem_fallback"|"off",
  profile: string|null, profile_id: uuid|null,   // comfortprofiel dat de zone volgt
  override: {until: ts|null, source: "app"|"button"|"remote_control"|...}|null,  // alleen bij control "manual"
  can_resume: bool,                  // "Terug naar schema" mogelijk (ook vanuit "off")
  setpoint_adjustable: bool,         // unit bereikbaar en niet uit
  pending_command: {id, status, since}|null,
  capabilities: {modes: [..], fan: bool, swing: bool, min_c, max_c, step_c},
  blocked_modes: [{mode, reason}],   // b.v. gedeelde buitenunit verwarmt al
  next_change: {at, target_c, mode, label}|null,
  correction_c: number,              // huidige bijsturing van het setpoint
  energy_priority: "comfort"|"balanced"|"saving",
  last_decision: string|null,
  watchdog_since: ts|null,           // begin van het huidige waakhondvenster (geen storing op zich)
  policy: Policy,
  notice: string|null, notice_level: "info"|"warn"|null   // korte uitleg om te tonen
}
Policy = {
  band_c: 0.5,                 // comfortband (0,2–3)
  min_interval_s: 900,         // minimale tijd tussen wijzigingen (300–7200)
  max_step_c: 1.0,             // maximale sprong per wijziging (0,5–3)
  max_starts_per_hour: 3,      // (1–6)
  stale_after_s: 1800,         // sensor verouderd na (300–7200)
  plausible_min_c: 5, plausible_max_c: 40,   // waarden daarbuiten negeren
  max_offset_c: 2.0,           // maximale bijsturing van het setpoint (0–3)
  override_default_minutes: 120,             // duur van handmatige bediening (15–1440)
  energy_priority: "balanced", // comfort reageert eerder, saving accepteert meer afwijking
  watchdog_minutes: 60,        // waakhond: 0 = uit, anders 15–720
  watchdog_min_progress_c: 0.3 // minimale vooruitgang binnen dat venster (0,1–3)
}
```

| Methode | Pad | Toegang | Body | Antwoord |
|---|---|---|---|---|
| GET | `/homes/{home_id}/zones` | `auth` | – | `[Zone]` |
| GET | `/zones/{id}` | `auth` | – | `Zone` |
| POST | `/zones/{id}/command` | `control` | `{mode?, target_c? (5–35), fan?: "auto"\|"low"\|"medium"\|"high", override_minutes? (5–1440)}` | 202 `{command_id, status: "queued"}`. Fouten: 409 `policy_denied`, `mode_required`, `unsupported`; 422 `validation_error`; 503 `device_offline` |
| POST | `/zones/{id}/resume` | `control` | – | `Zone`: handmatige bediening of "uit" weg, terug naar schema |
| GET | `/zones/{id}/history?hours=24` | `auth` | – | `{temperature: [[ts, v]], target: [[ts, v]]}` (`hours` 1–744) |
| GET | `/zones/{id}/log?limit=50` | `auth` | – | `[{at, kind, message}]`, nieuwste eerst (`limit` 1–500) |
| GET | `/zones/{id}/schedule` | `auth` | – | `{blocks: [Block], profile_id, profile}` |
| PUT | `/zones/{id}/schedule` | `admin` | `{blocks: [Block]}` (max. 100) | idem. Een eigen schema maakt de koppeling met een profiel los |
| PUT | `/zones/{id}/policy` | `admin` | deel van `Policy` | `Zone`. Alleen meegestuurde velden veranderen |
| PATCH | `/zones/{id}` | `admin` | `{name?, unit_device_id?, sensor_device_id?, outdoor_group?}` | `Zone`. Een andere unit begint de commandovolging opnieuw |
| GET | `/homes/{home_id}/climate/roles` | `auth` | – | `[{role, label, device_id, device_name}]`, b.v. `outdoor_temp` (buitentemperatuur) |

`mode: "off"` zet de zone bewust uit tot *Terug naar schema*. Elke andere opdracht is handmatige
bediening voor `override_minutes` (standaard `policy.override_default_minutes`). Alleen temperatuur of
ventilator wijzigen terwijl de airco uit staat, wordt geweigerd (`mode_required`): de unit zou anders
in een oude modus starten. De doeltemperatuur wordt afgerond op de stapgrootte van de unit.

**Schemablokken.**

```
Block = {days: [0..6], start: "HH:MM", target_c: 10–30, mode?: "heat"|"cool"|"auto"|"dry"|"fan"|"off"|null, label?: string}
```

- `days`: 0 = maandag … 6 = zondag. `start` in de tijdzone van de woning.
- Een blok geldt vanaf `start` tot het volgende blok begint, ook over middernacht en over de week heen.
- `mode` leeg of `null`: de unit houdt de modus waarin hij draait en krijgt alleen de nieuwe
  doeltemperatuur. Een unit die uit staat, wordt zo **nooit** aangezet.
- `mode: "off"` zet de unit om `start` uit. Een andere modus zet hem aan in die modus.
- Niet toegestaan (422): dezelfde dag twee keer in één blok, twee blokken op dezelfde dag en tijd, een
  modus die de unit niet kent, een temperatuur buiten het bereik van de unit.
- Na een handmatige bediening wacht het schema tot de override voorbij is of je *Terug naar schema*
  kiest. Een afstandsbediening telt ook als handmatige bediening.

### Comfortprofielen

Een profiel is een benoemde comfortinstelling: doel, band, modus, energieprioriteit en optioneel een
eigen weekschema. Een profiel toepassen vervangt het schema van de zone en zet de band en prioriteit;
een profiel aanpassen past het meteen toe op de zones die het volgen.

| Methode | Pad | Toegang | Body | Antwoord |
|---|---|---|---|---|
| GET | `/homes/{home_id}/profiles` | `auth` | – | `[Profile]` |
| POST | `/homes/{home_id}/profiles` | `admin` | `ProfileIn` | `Profile`. 409 `conflict` bij een dubbele naam |
| GET | `/profiles/{id}` | `auth` | – | `Profile` |
| PUT | `/profiles/{id}` | `admin` | `ProfileIn` | `Profile`; zones die het volgen krijgen het nieuwe schema meteen |
| DELETE | `/profiles/{id}` | `admin` | – | 204. Zones houden hun huidige schema. 409 `conflict` als een lopend experiment het profiel gebruikt |
| POST | `/zones/{id}/profile` | `admin` | `{profile_id}` | `Zone`. Een lopende handmatige bediening blijft staan |

```
ProfileIn = {name, target_c: 10–30, band_c?: 0.5, mode?: Mode|null,
             energy_priority?: "comfort"|"balanced"|"saving", schedule?: [Block]}
Profile = ProfileIn + {id, home_id, zone_ids: [uuid], created_at}
```

Een profiel zonder schema geldt de hele week (`00:00`, alle dagen) met `target_c` en `mode`.

## Commando's

Elke opdracht aan een apparaat (zone, lichtgroep, scène, Zigbee-koppelvenster) gaat als `queued` de
database in. Alleen de worker praat met apparaten; hij controleert het veiligheidsbeleid vlak voor het
verzenden opnieuw. De API antwoordt 202 met het `command_id`; de uitkomst komt via de WebSocket
(`command.updated`) en staat in `Zone.pending_command` of `LightGroup.last_command`.

| Status | Betekenis |
|---|---|
| `queued` | Wacht op de worker (ook bij een nieuwe poging na een fout) |
| `sending` | De worker verstuurt het nu |
| `sent` | Verstuurd, maar de unit liet de gevraagde stand nog niet zien; bij een latere uitlezing alsnog `confirmed`, anders na de time-out `failed` |
| `confirmed` | De uitlezing van het apparaat bevestigt de gevraagde stand |
| `failed` | Mislukt (apparaat gaf een fout, niet bereikbaar, te oud, worker herstart tijdens verzenden). Klimaatcommando's worden tot 2 keer opnieuw geprobeerd met hetzelfde id; daarna volgt een melding |
| `denied` | Vlak voor verzenden geweigerd door het beleid (b.v. de buitenunit draait inmiddels in een andere modus) |
| `cancelled` | Vervallen: nieuwer commando voor dezelfde zone, zone staat inmiddels op handmatig of uit, of apparaat gearchiveerd/vervangen |

Een commando is pas "uitgevoerd" bij `confirmed`; tot dan toont de UI "verzonden". Een mislukt
commando verandert nooit de regelstand van de zone.

## Verlichting en knoppen (Hue)

```
LightGroup = {id, name, on: bool, brightness: 0–100|null, scene: string|null, scene_id: uuid|null,
              reachable: bool, lights_on: int, lights_total: int, lights_reachable: int|null,
              owner_type, all_lights: bool, hidden: bool, last_command: CommandView|null}
Scene = {id, name, group_id, active: bool}
Light = {id, name, on, brightness, reachable, model, last_command: CommandView|null}
CommandView = {id, action, status, detail, at}
Button = {id, name, model, online, last_seen, battery,
          buttons: [{id, control_id, type, events: [string], bridge_rule: bool}],
          events: [string], bridge_rules: [id], bridge_rule_names: [string], last_event,
          mapping: [ButtonMapping]}
ButtonMapping = {id, button, event, mode: "lights"|"climate"|"combined", action: {steps: [Step]},
                 combined_confirmed: bool, enabled: bool, conflict: string|null}
Step = {type: "light", device_id, action: "set_group"|"set_light"|"toggle"|"recall_scene", params?: {on?, brightness?}}
     | {type: "zone", zone_id, mode?, target_c?, fan?, override_minutes?}
     | {type: "zone", zone_id, action: "resume"}
```

| Methode | Pad | Toegang | Body / antwoord |
|---|---|---|---|
| GET | `/homes/{home_id}/lights` | `auth` | → `{groups: [LightGroup], scenes: [Scene], lights: [Light]}` |
| POST | `/lights/groups/{id}` | `control` | `{on?, brightness? (1–100), scene_id?}` → 202 `{command_id, status: "queued"}`; 409 `policy_denied` |
| GET | `/homes/{home_id}/buttons` | `auth` | → `[Button]` |
| GET | `/buttons/{id}` | `auth` | → `Button` |
| PUT | `/buttons/{id}/mappings` | `admin` | `[MappingIn]` → `Button`: vervangt alle koppelingen |
| POST | `/buttons/{id}/mappings` | `admin` | `MappingIn` → `Button` |
| PATCH | `/button-mappings/{id}` | `admin` | deel van `MappingIn` → `Button` |
| DELETE | `/button-mappings/{id}` | `admin` | 204 |
| POST | `/buttons/{id}/simulate` | `admin` | `{button, event}` → `{button, event, would_run, bridge_rule, mappings: [{mapping_id, mode, conflict, skipped?, steps: [{type, result: "would_run"\|"refused"\|"invalid", reason, ...}]}], message}`. Voert niets uit |

`MappingIn = {button, event, mode, action: {steps: [Step]}, combined_confirmed?, enabled?}`.
Een koppeling `climate` mag alleen airco-stappen hebben, `lights` alleen licht-stappen, `combined` beide
en moet bewust bevestigd zijn (`combined_confirmed`). Twee actieve koppelingen op dezelfde knop en
gebeurtenis, of een niet-bevestigde gecombineerde actie, geven 409 `conflict` met `conflicts`. Voert
de Hue Bridge voor de knop zelf al een actie uit (`bridge_rule`), dan staat dat als waarschuwing in
`conflict`, maar opslaan mag. Ongeldige stappen geven 422 met `errors`.

## Energie

```
EnergySummary = {
  period: "day"|"week"|"month"|"year", start, end, timezone,
  total_kwh,                          // P1-import in de periode
  cost: number|null, cost_coverage: 0–1, fixed_cost: number|null, fixed_days, cost_note: string|null,
  currency,
  meters: [{role, label, kwh, unit: "kWh", cost, cost_coverage, quality, note, notes: [string]}],
                                      // role: grid, grid_export, airco, boiler, rest
  gas: {m3, unit: "m³", cost, cost_coverage, quality, note}|null,
  buckets: [{start, end, complete: bool, values: {role: kWh}, cost: number|null, notes: [string], gas_m3?}]
}
LivePower = {at, total_w: number|null, complete: bool, note: string|null,
             parts: [{role, label, watts: number|null, quality, at, stale: bool, note?}]}
Tariff = {id, valid_from: date, import_per_kwh, export_per_kwh, fixed_per_day,
          import_t1_per_kwh, import_t2_per_kwh, gas_per_m3, source: "handmatig"|...}
```

| Methode | Pad | Toegang | Body / antwoord |
|---|---|---|---|
| GET | `/homes/{home_id}/energy/summary?period=day&date=2026-11-08` | `auth` | → `EnergySummary` (`date` in de tijdzone van de woning; standaard vandaag) |
| GET | `/homes/{home_id}/energy/live` | `auth` | → `LivePower` of `null` (geen actuele meting) |
| GET | `/homes/{home_id}/tariffs` | `auth` | → `[Tariff]`, nieuwste eerst |
| POST | `/homes/{home_id}/tariffs` | `admin` | `TariffIn` → `Tariff`; 409 `tariff_exists` |
| PUT | `/tariffs/{id}` | `admin` | `TariffIn` → `Tariff` |
| DELETE | `/tariffs/{id}` | `admin` | 204 |
| GET | `/energy/limits` | `auth` | → `{grid, airco, boiler, gas}`: maximaal plausibel vermogen in kW (gas in m³/uur) |
| PUT | `/energy/limits` | `admin` | deel van `{grid, airco, boiler, gas}` (0,1–100; `null` = standaard) → idem |
| GET | `/homes/{home_id}/energy/export?format=csv\|json&from=&to=&resolution=hour\|day\|raw` | `auth` | Download (gestreamd), zie hieronder |

`TariffIn = {valid_from, import_per_kwh (0–5), export_per_kwh? (−1–5), fixed_per_day? (0–50),
import_t1_per_kwh?, import_t2_per_kwh?, gas_per_m3? (0–20)}`. Lege optionele velden mogen; een komma
als decimaalteken ook. T1 en T2 vul je samen in of allebei niet. Een tarief geldt vanaf `valid_from`
tot het volgende; bij T1/T2-prijzen gebruikt de app de gemeten verdeling over normaal en dal.

**Rollen.** `grid` (P1, huis totaal), `grid_export` (teruglevering van de P1), `airco` (kWh-meter van
beide units samen), `boiler` (Energy Socket, alleen meten) en `rest` (= `grid` − `airco` − `boiler`).
Per binnenunit bestaat geen meter-rol: de units delen één compressor.

**Kwaliteit.** Elk getal heeft een kwaliteitslabel:

- `measured`: rechtstreeks uit meterstanden, periode volledig gedekt.
- `derived`: berekend uit andere metingen die volledig zijn (normaal voor `rest`).
- `estimated`: er ontbreken metingen, een verdachte meterstand is overgeslagen, of een deelmeter mist
  (dan is ook `rest` geschat). `note`/`notes` zeggen waarom.
- `missing`: geen bruikbare meting.

Verbruik komt uit cumulatieve tellers, niet uit vermogen. Per uur bewaart de worker totalen met
**kwaliteitsvlaggen**, die in `notes` van meters en buckets terugkomen:

| Vlag | Tekst in `notes` | Betekenis |
|---|---|---|
| `gap` | ontbrekende metingen | Langer dan 20 minuten (gas: 2 uur) geen meterstand; het verschil is over de gat verdeeld |
| `incomplete` | – | Uur niet volledig gedekt (`complete: false`) |
| `suspect` | verdachte meterstand overgeslagen | Een stap boven het maximale vermogen van de rol (zie `/energy/limits`) telt niet mee |
| `reset` | tellerreset gedetecteerd | Teller ging terug (meter vervangen); pas geaccepteerd als volgende standen consequent stijgen |
| `estimated` | deels geschat | Afgeleide waarde moest begrensd worden of een deel ontbreekt |

Zomer- en wintertijd: een dag heeft dan 23 of 25 volledige uren. Kosten: `cost_coverage` is het deel van
het verbruik dat onder een tarief valt; ligt dat onder 100 %, dan zegt `cost_note` dat.

**Export.** Standaard uurtotalen per meter uit de uuraggregaten; `day` voor dagtotalen; `raw` voor de
opgeslagen metingen, maximaal 31 dagen per keer (anders 422 `range_too_large`). Zonder `from`/`to`: de
laatste 30 dagen tot vandaag. Elke regel valt in de periode waarin die meter aan zijn rol gekoppeld
was. CSV: puntkomma als scheidingsteken, komma als decimaalteken (opent zo in Excel), kolommen
`tijd_utc;tijd_lokaal;rol;apparaat;meting;waarde;eenheid;kwaliteit;opmerking`. JSON:
`{"schema":"shc.energy.v2", home, timezone, resolution, from, to, rows: [{at, role, device, metric, value, unit, quality, notes?}]}`.
Een ontbrekende waarde is een lege cel of `null`.

## Apparaten en integraties

```
Device = {id, provider, kind, name, stable_identity, model, firmware, room, room_id, integration_id,
          roles: [string], allowed_roles: [string], online, last_seen, capabilities: [string],
          protected: bool, compatibility: "tested"|"untested"|"unsupported", tested_at,
          lifecycle: "active"|"replaced"|"archived",
          calibration: {temperature_offset, humidity_offset}, details: {...}}
Integration = {id, provider, label, provider_label, home_id, enabled, identity, host,
               state: "ok"|"warn"|"error"|"not_configured", detail, last_ok_at, code,
               connected_since, missed_polls, checked_at, has_diagnostics}
```

| Methode | Pad | Toegang | Body / antwoord |
|---|---|---|---|
| GET | `/homes/{home_id}/devices?include_archived=false` | `auth` | → `[Device]` |
| GET | `/devices/{id}` | `auth` | → `Device` |
| PATCH | `/devices/{id}` | `admin` | `{name?, room_id?, lifecycle?: "active"}` (gearchiveerd apparaat terugzetten) → `Device` |
| PATCH | `/devices/{id}/calibration` | `admin` | `{temperature_offset? (−10–10), humidity_offset? (−30–30)}` → `Device`; alleen sensoren en airco-units |
| POST | `/devices/{id}/bind` | `admin` | `{role}` → `Device`. De vorige binding van die rol wordt gesloten, historie blijft. 409 `role_mismatch` |
| POST | `/devices/{id}/unbind` | `admin` | `{role}` → `Device`. 409 `not_bound` |
| DELETE | `/devices/{id}` | `admin` | → `Device`: archiveren. Rollen en zonekoppelingen eindigen, wachtende commando's vervallen, historie blijft |
| POST | `/devices/{id}/replace` | `admin` | `{new_device_id}` → `{old, new, moved: {roles, zones, mappings}}` |
| POST | `/devices/{id}/test` | `admin` | → `{device_id, ok, checked_at, checks: [Check], readings, note}`. Leest alleen; schakelt nooit iets. 503 `secret_key_invalid` als de koppeling inloggegevens heeft en de sleutel ontbreekt of te kort is |
| POST | `/devices/{id}/tested` | `admin` | `{note?}` → `Device` met `compatibility: "tested"` |
| GET | `/devices/{id}/diagnostics` | `admin` | → diagnoseblok (apparaat, integratie zonder sleutels, laatste metingen en commando's) |
| GET | `/integrations?home_id=` | `auth` | → `[Integration]`, plus een regel `not_configured` per aanbieder zonder koppeling |
| GET | `/integrations/compatibility` | `auth` | → `{providers, rows, note}`: de ondersteuningstabel uit [compatibility.md](compatibility.md) met de apparaten in dit huis |
| GET | `/integrations/{integration_id}` | `admin` | → `Integration` + `{health, config, diagnostics, devices}` |
| GET | `/integrations/{provider}/instructions` | `auth` | → `{provider, label, steps, needs_manufacturer_app, works_locally}` |
| POST | `/integrations/{provider}/discover` | `admin` | `{home_id, timeout_s? (1–30)}` → `[{provider, stable_identity, kind, name, host, model, firmware}]`; 502 `discovery_failed` |
| POST | `/integrations/{provider}/pair` | `admin` | `{home_id, host, name?, options?}` → `Device` + `{integration_id, repaired}`, of 202 `{status: "action_required", steps, retry_after_s}` (b.v. link-knop van de Hue Bridge). 503 `secret_key_invalid` (vóór het koppelen; er wordt niets opgeslagen) |
| PATCH | `/integrations/{integration_id}` | `admin` | `{host?, name?, verify?: true}` → `Integration`. Met `verify` moet op het nieuwe adres hetzelfde apparaat antwoorden (409 `identity_mismatch`) |
| DELETE | `/integrations/{integration_id}` | `admin` | 204: ontkoppelen (sleutel gewist, worker stopt) |
| POST | `/integrations/zigbee/setup` | `admin` | `{home_id}` → `Device` (kind `zigbee_bridge`) |
| POST | `/integrations/zigbee/permit-join` | `admin` | `{home_id, seconds? (30–254, standaard 120)}` → 202 `{command_id, open_until}` |

Rollen: `grid` (alleen een P1-meter), `airco` (kWh-meter of Energy Socket), `boiler` (Energy Socket,
beschermd: nooit schakelen), `outdoor_temp` (Zigbee-sensor), en de klimaatrollen van de zones.

**Adres bij koppelen.** Het adres staat alleen in `host`, nooit in `options` (422: "Geef het adres op
in het veld 'host', niet in de opties"). `host` moet een privé-IP-adres zijn of een naam op `.local`,
`.lan`, `.home.arpa`, `.localdomain`, `.home`, `.fritz.box` of `.box`. Geweigerd: loopback, link-local,
publieke adressen en de eigen netwerken van de app (`<SHC_NET_PREFIX>.250.0/29`, `.251.0/28`,
`.252.0/27`), tenzij het adres in `SHC_PAIRING_ALLOWED_HOSTS` staat (komma-gescheiden).

**Opnieuw koppelen.** Koppel je hetzelfde fysieke apparaat opnieuw (zelfde serienummer, MAC of
bridge-id), dan wordt de bestaande integratie bijgewerkt (`repaired: true`) in plaats van een tweede
gemaakt; oudere dubbele integraties van hetzelfde apparaat gaan uit. Zo herstel je ook een integratie
waarvan de sleutel niet meer past bij de hoofdsleutel (status *Sleutel past niet, koppel opnieuw*,
code `key_mismatch`).

Een mislukte koppeling wordt bewaard met diagnose (b.v. het scan-antwoord van een Gree-unit, nooit een
sleutel); de foutmelding bevat `integration_id`, `device_id` en `diagnostics`.

## Systeem

| Methode | Pad | Toegang | Body / antwoord |
|---|---|---|---|
| GET | `/health` | `public` | → `{status: "ok", version}` (controleert ook de database) |
| GET | `/system/status` | `auth` | → `{checks: [Check], version}`: database, achtergronddienst (hartslag en lussen), schijfruimte, back-up, tijdsynchronisatie, veilige modus, onderhoud, herstel en elke koppeling |
| GET | `/system/preflight` | `admin` | → `{checked_at, ready, checks}`: controles voor de installatiewizard (woning, achtergronddienst, MQTT, Zigbee-dongle, netwerkdetectie, klok, thuisadres, back-up). Leest alleen |
| GET | `/system/settings` | `auth` | → `{lan_url, remote_url, safe_mode, maintenance}` |
| PUT | `/system/settings` | `web-admin` | `{lan_url, remote_url}` → idem. Zelfde regels als bij koppelen: thuisadres in het thuisnetwerk, extern adres https. Leeg = `null` |
| POST | `/system/safe-mode/release` | `admin` | → `{safe_mode: false}`: apparaten kloppen, commando's mogen weer |
| GET | `/system/retention` | `admin` | → `{power_days, counter_days, log_days, sensor_days, source, defaults, minimums, maximum, note}` |
| PUT | `/system/retention` | `admin` | deel van `{power_days, counter_days, log_days, sensor_days}` (`null` = standaard) → idem |

**Bewaartermijnen** (dagen): `power_days` = vermogensmetingen (standaard 30, minimaal 7),
`counter_days` = ruwe meterstanden (standaard 365, minimaal 90), `log_days` = logboek van de
klimaatregeling (standaard 90, minimaal 7), `sensor_days` = alle andere ruwe metingen: temperatuur,
vochtigheid, doeltemperatuur, binnentemperatuur van de airco, batterij, signaalsterkte, luchtdruk en
batterijspanning (standaard 400, minimaal 30: het adaptieve model gebruikt de laatste 14 dagen). Elk
veld staat ook in `source` (`"app"` of `"env"`), `defaults` en `minimums`. De standaardwaarden komen uit
`SHC_RETENTION_*_DAYS` (ook `SHC_RETENTION_SENSOR_DAYS`, nooit lager dan het minimum); wat je in de app
instelt, gaat voor. Uurtotalen van energie en gas blijven altijd bewaard, en meterstanden die nog niet
in uurtotalen zijn verwerkt, worden nooit verwijderd. Meterstanden (stroom, gas, water) vallen nooit
onder `sensor_days`. De worker ruimt elk uur op, in porties.

**Veilige modus** staat aan na elk herstel: de worker stuurt geen enkel commando naar apparaten tot
een beheerder hem opheft. **Onderhoudsmodus** staat aan tijdens een herstel: de worker pauzeert.

### Back-ups

```
Backup = {id, kind: "export"|"db_dump", filename, status: "running"|"ok"|"failed", auto: bool,
          created_at, size_bytes, sha256, app_version, schema_revision, secrets_included,
          zigbee2mqtt_included, error, detail, downloadable: bool}
```

| Methode | Pad | Toegang | Body / antwoord |
|---|---|---|---|
| GET | `/system/backups` | `web-admin` | → `[Backup]`, nieuwste 100 |
| POST | `/system/backups` | `web-admin` | `{passphrase (12–256), include_secrets?: true}` → `Backup` (klaar binnen 50 s), of 202 `Backup` met `status: "running"` (daarna `GET /system/backups` volgen); 500 `backup_failed` |
| GET | `/system/backups/{id}/download` | `web-admin` | → het versleutelde bestand |
| GET | `/system/backup-settings` | `web-admin` | → `BackupSettings` |
| PUT | `/system/backup-settings` | `web-admin` | `{passphrase?, clear_passphrase?, hour? (0–23), keep? (1–365), enabled?, schedule?}` → `BackupSettings`. Weggelaten velden blijven zoals ze zijn. Met `passphrase`: 503 `secret_key_invalid` (er wordt niets opgeslagen) |

```
BackupSettings = {
  enabled, wanted, passphrase_set, passphrase_source: "app"|"file"|null,
  passphrase_error: string|null,          // opgeslagen wachtwoordzin past niet bij de hoofdsleutel
  hour, keep, timezone,
  schedule: "daily_full"|"dump_daily_full_weekly",
  next_kind, next_due_at, due_now,
  last_auto_backup, last_auto_dump,
  last_error, failures, retry_after,
  last_backup: Backup|null, zigbee2mqtt_in_last_backup: bool|null
}
```

De wachtwoordzin wordt versleuteld met de hoofdsleutel opgeslagen en nooit teruggegeven. Een back-up
draait in een apart proces met vlak geheugengebruik. Schema `daily_full` maakt elke dag een volledige
export (`.shcbak`); `dump_daily_full_weekly` maakt meestal een versleutelde database-dump (`.shcdump`,
kind `db_dump`, zonder Zigbee2MQTT-gegevens) en minstens elke 7 dagen een volledige export. Alleen
automatische back-ups worden opgeruimd (`keep` per soort). Een automatische back-up die nog liep toen de
worker (of zijn container) stopte, telt als mislukte poging: `failures` + 1 met dezelfde wachttijd,
`last_error` = "Back-up onderbroken (worker herstart)", en de bijbehorende `Backup` krijgt status
`failed`. Zie [onderhoud.md](onderhoud.md).

### Push-meldingen buiten de app

| Methode | Pad | Toegang | Body / antwoord |
|---|---|---|---|
| GET | `/system/notify` | `web-admin` | → `{configured, source: "app"\|"env"\|null, url_hint, error, events: [string]}`. 503 `secret_key_invalid` als er een adres in de app staat |
| PUT | `/system/notify` | `web-admin` | `{url: string\|null}` → idem. `null` of leeg wist het adres uit de app. 503 `secret_key_invalid` bij een adres (er wordt niets opgeslagen); wissen kan zonder sleutel |
| POST | `/system/notify/test` | `web-admin` | → `{ok, error}`; 409 `not_configured`; 503 `secret_key_invalid` als er een adres in de app staat |

`url` is een ntfy-onderwerp (`https://ntfy.sh/<geheim-onderwerp>` of een eigen ntfy in het thuisnetwerk)
of een andere webhook; een adres met `gebruiker:wachtwoord@` wordt geweigerd. Het adres wordt
versleuteld opgeslagen; `url_hint` toont alleen schema en host. Een adres in de app gaat voor
`SHC_NOTIFY_URL`. Past het opgeslagen adres niet bij de hoofdsleutel (herstel op een nieuwe NAS), dan
wordt het genegeerd en legt `error` uit dat je het opnieuw moet instellen. Berichten gaan alleen bij een
verandering: de achtergronddienst heeft langer dan 3 minuten geen hartslag (en weer wel), en automatische
back-ups mislukken of de laatste goede back-up is ouder dan 36 uur (en weer in orde).

### Herstellen en verhuizen

| Methode | Pad | Toegang | Body / antwoord |
|---|---|---|---|
| POST | `/system/restore/verify` | `web-admin` | Upload: multipart met `file` en `passphrase` (of een binaire body met header `X-Backup-Passphrase`) → `RestoreCheck`. Wijzigt niets. 422 `backup_invalid`, 413, 503 `secret_key_invalid`, 507 `disk_full` |
| DELETE | `/system/restore/uploads/{upload_id}` | `web-admin` | 204: upload weggooien (anders na 24 uur) |
| POST | `/system/restore/apply` | `web-admin` | `{upload_id, passphrase, mode?: "same_home"\|"new_home"}` → 202 `{job_id, status: "running", poll}`. 409 `backup_running`, `restore_running`, `restore_refused`; 422 `backup_invalid` |
| GET | `/system/restore/jobs/{job_id}` | `public` | → `{job_id, state: "running"\|"done"\|"failed", step, mode, started_at, finished_at, error, report}`. Zonder login: de database (met de sessies) wordt vervangen; het `job_id` is een lang willekeurig geheim |
| GET | `/system/restore/compare?home_id=` | `web-admin` | → `{restored_at, safe_mode, restored_from, devices: [...], functions: [...], summary: {compatible, partial, unavailable}}`: per apparaat en functie wat werkt na het herstel |

```
RestoreCheck = {upload_id, filename, size_bytes, format_version, created_at, app_version, schema_revision,
                current: {app_version, schema_revision}, schema_relation: "same"|"older"|"newer"|"unknown",
                restorable: bool, refusal: string|null, secrets_included, redacted, zigbee2mqtt_included,
                zigbee2mqtt, tables: {tabel: aantal}, total_records, homes, devices, integrations,
                credentials: {ok, failed: [naam], total, settings: {sleutel: "ok"|"failed"}, settings_failed},
                warnings: [string]}
```

- Een back-up van een **nieuwere** databaseversie wordt geweigerd (`restorable: false`); een **oudere**
  wordt na het terugzetten bijgewerkt, en het rapport noemt wat niet (volledig) is overgenomen
  (`report.dropped`: tabellen, kolommen en records die de update opruimde).
- `credentials` zegt of de apparaatsleutels en de versleutelde instellingen (wachtwoordzin voor
  back-ups, push-adres) te ontsleutelen zijn met de hoofdsleutel van **deze** server. Zo niet: oude
  sleutel terugzetten en `shc-api rekey --old-key-file`, of opnieuw koppelen / opnieuw instellen.
- `apply` zet de onderhoudsmodus aan (de worker pauzeert), vervangt alles in één transactie en zet daarna
  de **veilige modus** aan. Inlogsessies zitten niet in een back-up: log daarna opnieuw in.
- `mode: "new_home"` (verhuisd): de woningen uit de back-up worden gearchiveerd (historie blijft,
  koppelingen gaan uit), er komt een nieuwe woning "<naam> (nieuw)" bij met kamers en zones, en
  comfortprofielen en zoneschema's worden als concept overgenomen. Die zones staan uit tot *Terug naar
  schema*.

## Optimalisatie (opt-in)

| Methode | Pad | Toegang | Body / antwoord |
|---|---|---|---|
| GET | `/zones/{id}/adaptive` | `auth` | → `{enabled, baseline: {samples, running_samples, required, since, ready}, max_offset_c, models: [{id, version, params: {k_per_h, c_per_h, n, r2, usable}, created_at, active}]}` |
| POST | `/zones/{id}/adaptive/fit` | `admin` | → nieuwe modelversie, of 409 `insufficient_data` |
| POST | `/zones/{id}/adaptive/activate` | `admin` | `{model_id}` → status. Een oudere versie activeren = terugzetten. 409 `policy_denied` als het model niet betrouwbaar is |
| POST | `/zones/{id}/adaptive/disable` | `admin` | → status |
| GET | `/homes/{id}/experiments` | `auth` | → `[Experiment]` |
| POST | `/homes/{id}/experiments` | `admin` | `{name, zone_id, variants: [{label, start, end, profile_id?}] (2–4), auto_apply?: false}` → `Experiment` |
| POST | `/experiments/{id}/pause` | `admin` | → `Experiment`; 409 `conflict` als het al afgelopen is |
| POST | `/experiments/{id}/resume` | `admin` | → `Experiment`; 409 `conflict` als de zone op handmatig of uit staat |
| POST | `/experiments/{id}/stop?restore=true` | `admin` | → `Experiment` + `{zone_restored: "restored"\|"kept"\|"changed"\|"none"}` |
| DELETE | `/experiments/{id}?restore=true` | `admin` | 204 |
| GET | `/experiments/{id}` | `auth` | → `Experiment` + `{verdict, reasons, rows, outdoor, confounders}` |

```
Experiment = {id, name, zone_id, variants: [{label, start, end, zone_id, profile_id}],
              status: "defined"|"running"|"paused"|"finished"|"stopped",
              auto_apply: bool, current_variant: string|null, paused_reason: string|null, created_at}
Resultaat = {verdict: "vergelijkbaar"|"indicatief"|"onvoldoende", reasons: [string],
             rows: [{variant, days, kwh_per_day, vs_first_pct, mean_abs_dev_c, starts, outdoor_mean_c,
                     comfort_pct, other_zone_mean_target_c, notes}],
             outdoor: {device_id, name}|null, confounders: [string]}
```

Het adaptieve model stuurt maximaal ±0,5 °C bij, alleen als een versie actief is, en altijd binnen de
harde grenzen van de zone. Met `auto_apply` (elke variant een profiel, varianten mogen niet overlappen)
zet de worker het profiel van de lopende variant zelf op de zone. Bedien je de zone handmatig of
ontbreken temperaturen, dan pauzeert het experiment; een gepauzeerde variant houdt zijn profiel alleen
binnen zijn eigen tijdvenster. Na de laatste variant krijgt de zone zijn eigen profiel en schema terug.
Bij stoppen of verwijderen zet `restore=true` (standaard) het profiel en schema van vóór het experiment
terug; `restore=false` houdt wat de zone nu heeft. Een profiel dat iemand intussen met de hand koos,
blijft altijd staan. "Vergelijkbaar" alleen als de perioden lang genoeg zijn, de airco gemeten is en de
buitentemperatuur vergelijkbaar was; anders "indicatief" met de redenen.

## Realtime (WebSocket)

`GET /api/v1/ws?home_id=<id>`. Inloggen met de sessiecookie (alleen als de Origin overeenkomt met het
adres waarop de verbinding binnenkomt, inclusief poort), of als eerste bericht
`{"type":"auth","token":"shc_..."}` binnen 10 seconden (telefoons). Na het inloggen stuurt de server
`{"type":"hello","data":{user, via, client_role, can_control}}`. De client mag `ping` sturen en krijgt
`pong` terug.

Lukt inloggen niet, of wordt de sessie of het token later ingetrokken of verloopt het, dan stuurt de
server eerst `{"type":"error","data":{"code":..., "reason":...}}` en sluit daarna de verbinding:

| Sluitcode | Code in het foutbericht | Wat de client doet |
|---|---|---|
| 4401 | auth_required | Opnieuw inloggen (web-sessie verlopen of uitgelogd, geen of ongeldige inlog, Origin klopt niet) |
| 4403 | token_revoked / token_expired | Deze telefoon is niet meer gekoppeld: opnieuw koppelen met een QR-code |

Open verbindingen worden zo gesloten bij uitloggen, een wachtwoordwijziging, een uitgeschakelde of
verwijderde gebruiker en een ingetrokken telefoon. Elke minuut controleert de server bovendien of de
sessies en tokens van open verbindingen nog geldig zijn.

Berichten van de server: `{"type": "...", "data": {...}}`. Met `home_id` krijg je alleen berichten
van die woning (plus algemene).

| Type | Data | Wanneer |
|---|---|---|
| `zone.updated` | Zone | Na een wijziging, een nieuwe beslissing of een bevestigd commando |
| `lights.updated` | LightGroup | Nieuwe stand van een lamp of groep |
| `energy.live` | LivePower | Nieuwere vermogensmeting (maximaal elke 5 s per woning) |
| `alert.created` | Alert | Nieuwe open melding |
| `alert.resolved` | {id} | Melding opgelost: haal hem uit de lijst |
| `status.updated` | Overview.status | Een koppeling, de achtergronddienst, de back-ups, onderhoud of veilige modus veranderde |
| `command.updated` | {id, status, detail, attempts?} | Uitkomst van een commando (`confirmed`, `failed`, `denied` …), ook een nieuwe poging |
| `command.queued` | {id} | Een commando wacht op de worker |
| `integration.changed` | {id} | Een koppeling is toegevoegd, gewijzigd of verwijderd |
| `refresh` | {reason} | Het bericht was te groot om te versturen: haal alles opnieuw op |

De client verbindt opnieuw met backoff en haalt na elke nieuwe verbinding het volledige `overview`
opnieuw op. Intern gebruiken de API-processen ook `auth.revoked` om verbindingen te sluiten; dat bericht
gaat nooit naar clients.
