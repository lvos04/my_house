# Testlog

Wat is getest, hoe, en wat (nog) niet. Bijgewerkt: 8 november 2026, versie 0.1.0.

## Automatisch (draait in GitHub Actions bij elke push)

| Onderdeel | Tests | Hoe |
|---|---|---|
| Klimaatregelaar, energie, beleid, adaptief | 26 | Pure functies, `apps/api/tests/test_core_logic.py` |
| API + database + migraties | 16 | Echte PostgreSQL 16, `apps/api/tests/test_api.py` |
| Adapters (Hue, HomeWizard, Gree, Zigbee, TLS, resilience) | 46 | Fixtures op basis van officiële documentatie; Gree tegen een nagebootste UDP-unit; TLS tegen een echte lokale HTTPS-server |
| Web-app | 19+ | Vitest + Testing Library |
| Android-kern (`:core`) | 22 | JUnit + MockWebServer |
| Android-app | compile + APK | GitHub Actions (`huis-debug-apk`) |
| TrueNAS-YAML | 1 | Renderen + `docker compose config` |

### Scenario's uit plan §12 die automatisch gedekt zijn

- Boiler: alle routes (zone, lichtgroep, knop, regelaar, handmatig commando in de wachtrij) → geweigerd, ook als het commando al in de wachtrij stond (`test_boiler_socket_can_never_switch`, `test_boiler_never_switchable`).
- Twee units vragen verwarmen en koelen tegelijk → 409 met uitleg (`test_zone_command_policy_and_execution`, `test_shared_outdoor_conflict_blocks_cooling`).
- Afstandsbediening tijdens comfortregeling → automaat wacht; uitzetten wordt nooit automatisch teruggedraaid (`test_remote_control_change_becomes_override`, `test_remote_off_is_never_reactivated`).
- Verouderde of onwaarschijnlijke sensor → geen bijsturing, OEM regelt (`test_stale_sensor_…`, `test_implausible_sensor`).
- Geen snel pendelen: minimum interval en max. starts per uur (`test_min_interval_respected`, `test_max_starts_prevents_rapid_cycling`).
- P1 tellerreset, dubbele metingen, ontbrekende metingen, zomertijd 23/25 uur (`test_counter_reset_…`, `test_dst_day_has_25_hours`, `test_energy_summary_no_double_count_and_replacement`).
- Meter vervangen halverwege de dag: historie blijft per meter, rol telt beide (`test_energy_summary_no_double_count_and_replacement`).
- Back-up: verkeerde wachtwoordzin en één gewijzigde byte → geweigerd; herstel → veilige modus blokkeert commando's (`test_backup_corruption_refused_and_restore_safe_mode`).
- Login: geen standaardwachtwoord, CSRF, rate limiting, alleen-lezen rol, QR-koppeling eenmalig en intrekbaar.
- Experimenten: "besparing" alleen bij vergelijkbare perioden, anders "indicatief" met redenen.

## Handmatige rooktest (deze sessie)

API en worker als echte processen tegen PostgreSQL, met een nagebootste HomeWizard P1 op het netwerk:

- Beheerder aanmaken → woning → P1 koppelen via de API → rol *Huis totaal* → worker pollt elke 10 s → metingen in de database → live vermogen in de API → status *HomeWizard: Verbonden* → back-up maken → `verify-backup` toont alle tabellen.
- **Gevonden en opgelost:** (1) gekoppelde apparaten werden door de worker als nieuw apparaat op IP-adres gezien (instellingen van de adapter werden niet bewaard); (2) P1 met oudere firmware kon niet koppelen omdat alleen API v2 werd geprobeerd; (3) een validatiefout met eigen melding gaf 500 in plaats van 422. Voor alle drie staat er nu een test.

## Nog niet getest

- **Echte apparaten:** Hue Bridge, Gree-units, HomeWizard, Zigbee-dongle en -sensoren. Status in de app: *nog niet getest*. Zie [compatibility.md](compatibility.md).
- **Gree:** of jouw modellen/firmware het lokale protocol ondersteunen (compatibiliteitsgate fase 2).
- **Docker-images en TrueNAS:** in deze omgeving draait geen Docker-daemon. De images worden in GitHub Actions gebouwd; installatie op TrueNAS 25.10, reboot en update/rollback moeten nog op je NAS worden uitgevoerd.
- **USB opnieuw insteken, MQTT-herstart, PostgreSQL-herstart:** de code herstelt met backoff (worker-lussen, LISTEN-verbinding, MQTT-reconnect), maar dit is nog niet op echte hardware nagespeeld.
- **Browser end-to-end** (Playwright) tegen de volledige stack: nog niet; de web-tests draaien op componentniveau.
- **Android op een toestel:** QR-scanner, widget en meldingen.

## Eerste test op je NAS (checklist)

1. Installeren volgens [installatie-truenas.md](installatie-truenas.md); **Systeem** toont alles groen behalve back-up.
2. Back-up maken, app herstarten, TrueNAS rebooten: inloggen werkt, gegevens zijn er nog.
3. Hue koppelen; een groep aan/uit vanuit de app; knop indrukken en event zien.
4. HomeWizard P1 en socket koppelen, rollen toewijzen; na een uur een kloppend uurtotaal.
5. Gree: unit zoeken. Lukt binden en uitlezen? Dan één setpointwijziging en controleren dat de app "bevestigd" toont.
6. Zigbee (na aankoop dongle): sensor koppelen, uit bereik halen → na 30 min melding *verouderd*.
7. Herstel oefenen op een tweede stack (zie [onderhoud.md](onderhoud.md)).
