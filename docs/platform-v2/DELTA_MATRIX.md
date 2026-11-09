# Delta-matrix — my_house (B06) — fase P0

Peildatum **2026-11-09** · repo `lvos04/my_house` · branch `ccr-c133d7a0-kuz1cv` · code-HEAD **`b0a6a98`**
(`VERSION` 0.2.0; zie ADR-0001: delta tegen 0.2.0, niet tegen de pakketreferentie v0.1.0). Bron van de eisen:
`02_BOUWPLANNEN/B06_MY_HOUSE_DELTA.md`, `EXCEL_TAKEN.json` (B06-001…009), `03_INSTALLATIE/I05_MY_HOUSE.md`,
`04_BESTAANDE_APPS/A20_MY_HOUSE.md`, `05_TESTS/INTEGRALE_E2E_TESTMATRIX.md` en `PLATFORM_CONTRACTEN.md` **1.1**.
Feitenbasis: `INVENTARIS.md` (zelfde map); paden in de code zijn relatief aan `apps/api/src/shc_api/` tenzij anders
vermeld, testpaden aan `apps/api/tests/` of `integrations/tests/`.

**Legenda.** **Bestaand** = in code + geautomatiseerde test (met nep-apparaten/simulators — **nooit** op echte hardware
of de echte NAS gevalideerd). **Onvolledig** = deels aanwezig; wat mist staat erbij. **Ontbrekend** = niet aanwezig.
**Geblokkeerd** = alleen uit te voeren met echte hardware/NAS/accounts (D01–D18 onbekend). **N.v.t.** = hoort niet bij
my_house (eigenaar staat erbij). "Niet doen" = grens uit `02_DATA_EIGENAARSCHAP`, `03_RISICO_AUTONOMIE` of contract §12.

In fase P0 is **geen productcode** gewijzigd. Alle "minimale wijzigingen" zijn voorstellen voor P3 (nummers P3-xx
verwijzen naar §9).

---

## 0. Samenvatting

- my_house 0.2.0 dekt de **functionele** kern van B06 al grotendeels: boilerblokkade op drie lagen, gedeelde
  buitenunit, afstandsbediening-override, sensorterugval, readback-semantiek, Gree-protocolgate, energie uit tellers
  met kwaliteitslabels/reset/zomertijd, rolbindingen los van apparaten, versleutelde back-up/herstel/verhuizen. Alles
  alleen getest met nep-apparaten (baseline: 319 + 188 + 164 tests geslaagd, `INVENTARIS.md` §15) —
  **NIET GEVALIDEERD (hardware)**.
- **Grootste hiaten** (P3):
  1. **Installatie**: my_house is **niet** als standaard TrueNAS Custom App met één image te installeren; het vraagt nu
     `install.sh` (root, shell) + compose-YAML via *Install via YAML* — strijdig met contract 1.1 (P3-01, ADR-0004).
  2. **Hub-integratie**: geen `/connector/v1/*`, geen dienstidentiteit, geen outbox/events, geen `/healthz`/`/readyz`,
     geen verificatie van een ondertekend `ExecutionRequest`, geen `Idempotency-Key` (P3-03…P3-06, ADR-0002).
  3. **Inbedrijfstellingsgate**: het label `tested/untested` blokkeert bediening niet (B06-001, P3-07, ADR-0005).
  4. **Release-hygiëne**: `:latest`-tag, base-images zonder digest, 0.2.0 niet gepubliceerd (P3-02).
- B06-009 (hardwarecontrole) is **Geblokkeerd** tot echte apparaten en de NAS beschikbaar zijn.
- Telling over de 9 B06-taken (§1): 0 op hardware gevalideerd; **bestaand met alleen live-validatie open**: B06-004,
  B06-005; **onvolledig**: B06-001, B06-002, B06-003, B06-007, B06-008; **grotendeels ontbrekend**: B06-006;
  **geblokkeerd**: B06-009.

---

## 1. B06-taken (Excel) — per eis

### B06-001 — Productiecompatibiliteit (F0, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging (P3) | Niet doen |
|---|---|---|---|---|
| Testmatrix per apparaatmodel, firmware, protocolversie, LAN-IP, detectie en terugval | **Onvolledig** | `docs/compatibility.md` (tabel per model/protocol, alles "nog niet getest met echte hardware"); als data: `GET /integrations/compatibility` (`routers/devices.py:782-940`), test `test_ws_devices_api.py::test_compatibility_matrix_as_data`; per apparaat `model`, `firmware`, `compatibility`, `tested_at` (`models.py:176-200`); detectie: `GET /system/preflight` met mDNS-probe (`routers/homes.py:138-224`), `test_ws_devices_api.py::test_preflight_checks` | Kolommen **LAN-IP/DHCP-reservering, detectiemethode (scan/handmatig) en terugval** ontbreken in de matrix; P3-07: `docs/compatibility.md` + `/integrations/compatibility` uitbreiden met `protocol_version`, `host` (alleen gemaskeerd), `discovery: scan\|manual`, `fallback` (bijv. "afstandsbediening blijft werken") en een **testlog per apparaat** (wie/wanneer/firmware). Echte waarden **Geblokkeerd** (D12–D14) | Geen voorbeeldmodellen/IP's als echte waarde invullen |
| Scherm: apparaatkaart met compatible/tested/unsupported + datum | **Bestaand** | `apps/web/src/devices/DeviceCard.tsx` (label `COMPATIBILITY_LABEL`), `Compatibility.tsx`; `POST /devices/{id}/tested` (`routers/devices.py:649-678`) | — | — |
| Acceptatie: **geen ongeteste Gree-bediening** | **Onvolledig** | Protocolgate bestaat (scan+bind+status, anders `unsupported`): `gree/adapter.py:380-410`, `test_gree_bind.py::test_bind_failure_is_unsupported_with_scan_diagnostics`, `::test_status_failure_after_bind_is_unsupported`. Maar `compatibility` wordt **niet** gecontroleerd bij bediening (`policy.py:37-54` kent het veld niet) | P3-07 (ADR-0005): `DeviceFacts.compatibility`; `check_command` weigert bediening van `untested` apparaten met 409 `policy_denied` (API, worker én connector); zone toont reden; regelaar valt terug op OEM. Tests: `untested` → 409, `tested` → 202, leestest schakelt niets | Niet automatisch `tested` zetten op basis van sim/CI |
| Acceptatie: **Hue en P1 als eerste read-only** | **Onvolledig** | P1/meters kunnen nooit schakelen (`MEASURE_ONLY_KINDS`, `policy.py:18`); Hue kan direct na koppelen bediend worden | Valt onder P3-07: Hue-bediening pas na *getest*; wizardvolgorde (meters → Hue lezen → Hue test → Gree na D12) in `apps/web/src/pages/Install.tsx` en `I05` | — |
| Foutafhandeling: externe storing nooit als succes; bewijs (scenario, image-versie, screenshots, geredigeerde request/response, tijden, audit/rollback, incidentlink) | **Onvolledig** | Mislukte koppeling bewaart diagnose zonder sleutels (`routers/devices.py:1100-1188`, `test_ws_devices_api.py::test_pair_failure_keeps_diagnostics_and_maps_errors`); `GET /devices/{id}/diagnostics`; commando's `failed`/`denied` nooit als succes (`docs/api.md:325-343`) | Incidentlink naar de Hub bestaat pas met events (P3-04); bewijs op echte apparaten **Geblokkeerd** | — |

### B06-002 — Apparaatconnecties (F1, P0)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Hue local API v2 + eventstream | **Bestaand** | `integrations/src/shc_integrations/hue/`; `test_hue.py`, `test_hue_adapter.py::test_eventstream_watchdog_reconnects_and_reloads`, `test_contract_hue.py` (HTTPS met vastgepind certificaat, `test_tls*.py`) | Echte bridge **Geblokkeerd** (hardware) | Geen tweede Hue-sessie vanuit de Hub |
| HomeWizard local API v1/v2 | **Bestaand** | `homewizard/adapter.py`; `test_homewizard.py::test_pair_v2_requires_button_then_token`, `::test_pair_falls_back_to_v1_on_old_firmware`, `test_homewizard_identity_time.py` | Echte meters **Geblokkeerd** (D14) | — |
| Gree UDP opt-in bind/readback | **Bestaand** | `gree/adapter.py`, `gree/protocol.py`; `test_gree.py`, `test_gree_bind.py`, `test_contract_gree.py`; `infra/e2e/fake_gree_unit.py` + `infra/e2e/worker-command.e2e.ts` (CI-job `stack`) | Echte units **Geblokkeerd** (D12). "Opt-in": koppelen is een expliciete beheerdersactie; bediening na ADR-0005 pas na *getest* (P3-07) | — |
| Zigbee2MQTT via MQTT | **Bestaand** | `zigbee/`; `test_zigbee.py`, `test_zigbee_pairing.py`, `test_contract_zigbee.py`, API `test_ws_failures.py::test_zigbee_mqtt_disconnect_and_reconnect`, `::test_zigbee_bridge_offline_raises_the_dongle_alert` | Dongle niet aanwezig/gekocht (D13) → **Geblokkeerd**; in de alles-in-één image is Zigbee2MQTT een open ontwerppunt (ADR-0004 punt 6) | — |
| mDNS/broadcast-beperking in containers | **Bestaand** (gedocumenteerd + gemeten) | preflight-check *Netwerkdetectie* (`routers/homes.py:138-224`); handmatig IP werkt altijd (`docs/installatie-truenas.md` §Ontdekken) | In de Custom App (brug-netwerk van TrueNAS) opnieuw meten — **NIET GEVALIDEERD (echte NAS)** | Geen `host_network: true` zonder reden (manifest §9) |
| DHCP-reservering | **Bestaand** (documentatie) | `docs/installatie-truenas.md` §Ontdekken; herkenning op serienummer/MAC: `PATCH /integrations/{id}` met `verify` → 409 `identity_mismatch` (`test_ws_devices_api.py::test_patch_integration_address_checks_identity`) | — | — |
| serial-by-id voor de dongle | **Bestaand** (compose) / **Onvolledig** (Custom App) | `compose.yaml:195-219` (`/dev/serial/by-id/…`), preflight `zigbee_dongle` | In de alles-in-één Custom App: apparaat-doorgifte via het formulier — **te verifiëren** tegen de TrueNAS-docs van de echte versie (D01, ADR-0004) | — |
| Scherm: testknop per adapter en apparaat met feedback | **Bestaand** | `POST /devices/{id}/test` (alleen lezen, `routers/devices.py:527-647`), `apps/web/src/devices/DeviceTools.tsx` (`DeviceTestButton`), integratiestatus in `GET /integrations`; `test_ws_devices_api.py::test_device_test_probe_tested_and_diagnostics` | — | De testknop schakelt nooit iets |
| Acceptatie: **zonder readback nooit "command uitgevoerd"** | **Bestaand** | `CommandResult.confirmed` (`integrations/src/shc_integrations/base.py`), statussen `sent/confirmed/failed` (`docs/api.md:332-343`); contract-kit "bevestigd alleen bij readback"; `test_gree.py::test_readback_mismatch_not_confirmed`, `test_hue_adapter.py::test_put_errors_mean_not_confirmed_even_if_readback_matches`, `test_ws_climate_worker.py::test_unconfirmed_command_is_confirmed_by_a_later_readback` | — | — |
| Acceptatie: **reconnect na netwerkonderbreking** | **Bestaand** (sim) | `integrations/src/shc_integrations/resilience.py`; `test_resilience.py`, `test_ws_failures.py::test_device_offline_then_back_debounced`, `::test_zigbee_mqtt_disconnect_and_reconnect`, `::test_worker_loop_survives_a_dropped_database_connection`, `test_hue_adapter.py::test_eventstream_watchdog_reconnects_and_reloads` | Echte netwerkonderbreking op de NAS **Geblokkeerd** | — |
| Flaky test | **Onvolledig** | `test_ws_devices_core.py::test_push_stream_batches_and_restarts` hing in run 1 (`INVENTARIS.md` §15) | P3-12: `asyncio.wait_for`-grens in de test en in `_pump` waar zinvol; oorzaak analyseren | Test niet uitschakelen |

### B06-003 — Harde boilerschakelblokkade (F1, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Energy Socket is uitsluitend energiemeter; app mag niet schakelen | **Bestaand** | API: `policy.check_command` (`policy.py:37-54`) in `commanding.queue_command` (:84-90) en `request_zone_command` (:154-163); worker vlak voor verzenden (`worker/commands.py:140-146`); adapter: `relay_allowed` = altijd `False` (`homewizard/adapter.py:239-246, 500-506`; `worker/core.py:486`; `routers/devices.py:491`). Tests: `test_api.py::test_boiler_socket_can_never_switch`, `test_core_logic.py::test_boiler_never_switchable[*]`, `::test_energy_socket_relay_blocked_even_without_role`, `test_homewizard.py::test_v1_socket_poll_and_relay_blocked_by_default`, `::test_relay_allowed_only_via_policy_hook` | — | Beleid niet versoepelen; geen `relay_allowed`-hook die `True` kan geven |
| Worker | **Bestaand** | `worker/commands.py:140-146`; een al in de wachtrij gezet `set_relay` wordt `denied` (`test_api.py::test_boiler_socket_can_never_switch`) | — | — |
| **Centraal gedelegeerd commando** (Hub/AI/flow) | **Ontbrekend** | Er is geen Hub-route; een Hub met een `control`-token zou de bestaande routes gebruiken (die wel weigeren) | P3-05/P3-06: connector `propose`/`apply` roepen `check_command` aan; doel met beschermde rol of meter → **altijd** 409 `policy_denied`, ook met geldige handtekening, goedkeuring of `actor.type=ai`; `boiler.*`/`set_relay` staat niet in de allowlist | Geen eigen boilerbeleid in de connector naast `policy.py` |
| Acceptatie: **POST vanuit Hub/admin/AI → altijd `policy_denied`** | **Onvolledig** | Zone-route met boiler als unit → 409 `policy_denied` (via `check_command`); `POST /lights/groups/{boiler-id}` → **404** (`routers/lights.py:51`); knopkoppeling naar boiler → simulate `refused`/worker `denied` | P3-06: gelijktrekken: elke bedieningsroute die een beschermd apparaat als doel krijgt, antwoordt 409 `policy_denied` vóór de type-controle; `PATCH /zones/{id}` accepteert als unit alleen `kind=="ac_unit"` (`routers/zones.py:276-282`). Regressietest **EX27** over alle routes: web-sessie admin, telefoon admin/control, connector (Hub), connector met `actor.type=ai`, knop, schema, regelaar | — |
| Scherm: boilerkaart met vermogen/kWh/kosten, geen schakelknop | **Bestaand** | Energie-pagina (`apps/web/src/energy/*`, rol `boiler`), `apps/web/src/components/setup/Assign.tsx:128` ("de app schakelt hem nooit"); geen relay-knop in de UI | Hub-kaart (B02) moet dezelfde regel volgen — eigenaar `ai-server-manager` | — |
| Bewaking: boilerstekker staat uit / *Switch lock* uit | **Bestaand** | `worker/ingest.py:406-441`; `test_ws_devices_worker.py::test_protected_socket_relay_and_switch_lock_alerts` | Event `home.boiler.relay_off` naar de Hub (P3-04) | Niet zelf "aanzetten" om te herstellen |

### B06-004 — Gree klimaat en gedeelde buitenunit (F2, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Twee binnenunits delen compressor: conflicterende heat/cool voorkomen | **Bestaand** | `climate.py:165-216` (`modes_conflict`, `blocked_modes`, `validate_request`), advisory lock per buitenunit `commanding.py:63-71`, worker beslist groepen samen `worker/climate_loop.py:69-122`; tests `test_core_logic.py::test_shared_outdoor_conflict_blocks_cooling`, `test_ws_climate_worker.py::test_two_zones_on_one_outdoor_unit_are_decided_together`, `::test_offline_and_auto_units_still_block_conflicting_modes`, `test_ws_climate_engine.py::test_auto_conflicts_with_heat_and_cool` | Echte units **Geblokkeerd** (D12) | Geen tweede regelaar in de Hub |
| Acceptatie: **heat/cool-conflict → HTTP 409** | **Bestaand** | `commanding.py:175-177` → 409 `policy_denied` "…buitenunit…"; `test_api.py::test_zone_command_policy_and_execution` (409), `test_ws_climate_worker.py::test_two_zones_on_one_outdoor_unit_are_decided_together` (409 + `denied` vlak voor verzenden) | Ook via connector (P3-05) | — |
| Handmatige remote-overrides bewaren; **remote-off blijft off** | **Bestaand** | `climate.py:297-310` + `decide`; `test_core_logic.py::test_remote_off_is_never_reactivated`, `::test_remote_control_change_becomes_override`, `test_ws_climate_engine.py::test_remote_change_during_off_becomes_manual`, `::test_difference_needs_two_consecutive_readbacks`, `test_ws_climate_worker.py::test_remote_override_expires_back_to_schedule_without_loop`, `::test_engine_command_discarded_after_user_turned_zone_off` | — | AI/Hub mag een remote-off nooit opheffen; `zone.resume` alleen met goedkeuring (R2) |
| Sensor-stale-terugval; **stale Zigbee stopt optimalisatie** | **Bestaand** / melding **Onvolledig** | `climate.py:219-228, 492` (`oem_fallback`, correctie 0); `test_core_logic.py::test_stale_sensor_falls_back_to_oem_and_no_correction`, `test_ws_climate_engine.py::test_invalid_sensor_resets_the_correction`; melding `stale:{device}` in `worker/health.py:231-263` **zonder eigen test** | P3-08: test voor `sensor_round` (melding na `stale_after_s`, opgelost bij nieuw bericht) — dekt EX29 | — |
| Veilig minimuminterval | **Bestaand** | `DEFAULT_POLICY` (`climate.py:40-53`: `min_interval_s`, `max_step_c`, `max_starts_per_hour`); `test_core_logic.py::test_min_interval_respected`, `::test_max_starts_prevents_rapid_cycling`; Gree ≥ 2 s tussen commando's `test_gree.py::test_rate_limit_between_commands` | — | — |
| Scherm: modus, target, actieve sensor, freshness, override en reden van blokkade | **Bestaand** | `Zone`-model (`docs/api.md:226-251`: `temperature.at/stale/quality`, `override`, `blocked_modes[{mode,reason}]`, `notice`, `last_decision`); `views/zones.py:90-111`; web `apps/web/src/climate/*`, `apps/web/src/test/climate-overview.test.tsx` | — | — |

### B06-005 — Energiedatakwaliteit (F2, P1)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| P1 totaal, boilerplug, airco 1-fase kWh-meter; **rollen los van fysieke devices** | **Bestaand** | rollen `grid`, `grid_export`, `airco`, `boiler`, `rest` (`docs/api.md:420-422`), `logical_roles` + `device_bindings` met geldigheid (`models.py:203-221`); `POST /devices/{id}/bind\|unbind\|replace`; `test_ws_devices_api.py::test_bind_only_fitting_roles_and_unbind`, `::test_archive_restore_and_replace` | Echte meters **Geblokkeerd** (D14) | — |
| Cumulatieve tellers + kwaliteitslabels; opsplitsen; alleen aggregeren als bronnen vergelijkbaar zijn | **Bestaand** | `energy.py`, `worker/aggregates.py` (`energy_hourly` met vlaggen `gap/incomplete/suspect/reset/estimated`), `rest` = grid − airco − boiler met `estimated` als een deelmeter mist; `test_ws_energy_aggregates.py::test_aggregates_match_raw_and_summaries_read_from_aggregates`, `::test_live_power_shows_stale_submeter_and_live_round_publishes` | — | Hub (B14) rekent niet opnieuw; leest my_house |
| Datumcorrecties meterreset/zomertijd | **Bestaand** | `test_core_logic.py::test_counter_reset_and_duplicates_and_gap`, `::test_dst_day_has_25_hours`, `test_ws_energy_aggregates.py::test_reset_only_accepted_when_following_readings_rise_from_new_base`, `::test_dst_days_are_complete_and_counted_in_real_hours[*]`, `integrations/tests/test_homewizard_identity_time.py::test_autumn_double_hour_resolves_to_the_right_instant`, `::test_spring_gap_and_late_device_clock` | — | — |
| Acceptatie: **meters vervangen → historie correct** | **Bestaand** | `test_api.py::test_energy_summary_no_double_count_and_replacement`, `test_ws_energy_aggregates.py::test_export_streams_hourly_aggregates_and_respects_bindings` | — | — |
| Acceptatie: **geen verbruik per binnenunit verzinnen** | **Bestaand** | geen meter-rol per binnenunit (`docs/api.md:420-422`, `README.md` "Bewuste grenzen") | Connector-`items` mag ook geen per-unit-veld krijgen (P3-03) | Geen schatting per binnenunit, ook niet in de Hub |
| Scherm: dag/week/maand/jaar, prijs, verbruiksbron, realiteit vs schatting | **Bestaand** | `GET /homes/{id}/energy/summary?period=` met `quality`/`notes`/`cost_coverage`; web `apps/web/src/energy/*`, `apps/web/src/test/energy-page.test.tsx`; tarieven incl. T1/T2/gas `test_ws_energy_aggregates.py::test_costs_split_tariffs_fixed_costs_partial_coverage_t1_t2_and_gas` | — | — |
| Energie naar de Hub (B14) | **Ontbrekend** | — | P3-03/P3-04: connector-`items?type=energy_total` (uit `energy_hourly`, met `quality`) + event `energy.reading.recorded` (uurlijks) | Geen ruwe metingen dupliceren in de Hub; alleen cache + metadata |

### B06-006 — Hub-integratie (F3, P1)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Hub toont woning, zones, meldingen, energie, metingen | **Onvolledig** | Alle data bestaat via leesroutes op niveau `auth` (§6): `GET /homes/{id}/overview` (`routers/homes.py:75`), `/homes/{id}/zones`, `/lights`, `/energy/summary`, `/energy/live`, `/alerts`, `/devices`, `/system/status`; elk meetpunt heeft `at` + `quality` + `stale` | P3-04: dienstidentiteit (koppelcode met rol `viewer`, `api_clients.kind=service`, migratie 0005); P3-03: `/connector/v1/discover\|health\|items\|events\|evidence` + `/healthz` + `/readyz` (contractfoutformaat) | Geen kopie van de my_house-database in de Hub |
| Read-only overview/events | **Onvolledig** | WebSocket met token (`routers/ws.py:89`) zonder replay; geen outbox | P3-04: outbox (`connector_outbox`) gevuld waar nu `events.publish`/`raise_alert` gebeuren; `GET /connector/v1/events?cursor=`; optioneel push naar `POST {HUB_URL}/api/v1/events/inbox` na enrollment | Geen exact-once-aanname |
| **Commando's alleen via my_house-API met permissie, TTL en readback** | **Onvolledig** | Permissie (`control`) en readback (`confirmed`) bestaan; **TTL, nonce, idempotency en Hub-handtekening ontbreken** | P3-05: `POST /connector/v1/propose\|apply` met ondertekend `ExecutionRequest` (Ed25519), vervaltijd, nonce, `Idempotency-Key`, ETag → roept bestaande `request_zone_command`/`queue_command` aan; evidence met readback (ADR-0002) | Hub krijgt nooit een `control`-token |
| Geen dubbele eigen Gree-controller in de Hub | **N.v.t.** (grens) | — | — | Hub bouwt geen Gree-/klimaatlogica (eigenaar my_house) |
| Scherm: compacte huiskaart + drilldown/quick controls die rollen volgen | **N.v.t.** voor my_house | eigenaar `ai-server-manager` (Hub-UI); my_house levert data + deep-link (`lan_url` uit `GET /system/settings`) | `discover` geeft `deep_link` en de toegestane acties per rol (P3-03) | — |
| Acceptatie: **my_house offline → oude data met stale-markering** | **N.v.t.** voor my_house (Hub-cache) / **Onvolledig** (bron) | my_house levert `at`/`stale` per meting | `/connector/v1/health` met `observed_at` en `status: unknown` bij twijfel; de Hub markeert zelf | — |
| Acceptatie: **Hub kan niet schakelen** | **Onvolledig** | Met een `viewer`-token: 403 op alle bedieningsroutes (`deps.py:159-163`; `test_foundation.py::test_viewer_cannot_resume_or_control`) | P3-04: `service`-clients nooit hoger dan `viewer`; alleen via ondertekende connector-apply (P3-05). Test: service-token → 403 op `/zones/{id}/command`, `/lights/groups/{id}` | — |
| Credential verlopen/ingetrokken → nooit open toegang (EX16) | **Bestaand** | 401 `token_revoked`/`token_expired` (`deps.py:52-113`), `test_ws_security_pairing.py`, `test_api.py` (intrekken → 401) | Hub-kant: connector `unknown` + incident (B02) | — |

### B06-007 — Spraak en AI (F3, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| AI zet alleen een veilige schema-aanbeveling of lichtcommando op; klimaat high-impact met goedkeuring; boiler denied | **Ontbrekend** (my_house-kant) | Geen AI/connector; boiler wordt overal geweigerd (B06-003) | P3-05: allowlist met per actie een JSON-parameterschema in `discover` (`light.group.set`, `light.scene.recall`, `zone.command`, `zone.resume`, `zone.schedule.propose` = alleen concept); risicoklasse bepaalt de Hub-policy (R1 licht, R2 klimaat); my_house negeert `risk_claim` | Geen AI-tool die rechtstreeks de bestaande `control`-routes gebruikt |
| Tool-schema's per zone-ID, gebruikersrol | **Ontbrekend** | — | `discover.actions[]` met `target_type`, `params_schema`, `min_role`; `items?type=zone` met stabiele zone-ID's | — |
| Voor/na-bevestiging, fysieke-stand-controle | **Onvolledig** | Readback bestaat (`confirmed`); voor-stand staat in `Zone` | `propose` geeft `before` (huidige stand + `resource_etag`), `evidence/{command_id}` geeft `after` (readback) + hashes | — |
| Scherm: chat antwoordt met bevestigde apparaatstatus, "verstuurd ≠ klaar" | **N.v.t.** voor my_house (chat in Hub, B15/B02) | my_house onderscheidt `sent` en `confirmed` (`docs/api.md:332-343`) | evidence-route levert de status (P3-05) | — |
| Acceptatie: **onbetrouwbare Gree-readback → actie mislukt, geen schijnsucces** | **Bestaand** (bron) | `test_gree.py::test_readback_mismatch_not_confirmed`, `test_ws_climate_worker.py::test_failed_command_retries_alerts_and_keeps_control`; commando blijft `sent` → `failed` na time-out | Doorzetten naar connector-evidence (P3-05) | — |
| Spraak onzeker (EX49) | **N.v.t.** voor my_house | Hub (B15): transcript + preview | my_house voert alleen ondertekende, goedgekeurde verzoeken uit | — |

### B06-008 — Verhuisbaarheid (F4, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Export versleutelde woningconfig, logische apparaat-ID's en rolmapping | **Bestaand** | `.shcbak` v2 (scrypt + AES-256-GCM, manifest + sha256; `backup.py:1-28, 808-968`), bevat alle tabellen incl. `devices.stable_identity`, `logical_roles`, `device_bindings`; `test_ws_backup_format.py`, `test_api.py::test_backup_corruption_refused_and_restore_safe_mode` | — | Geen tweede exportformaat in de Hub (B12 gebruikt dit) |
| Importwizard + dry-run | **Bestaand** | `POST /system/restore/verify` (wijzigt niets), `apply` (`same_home`/`new_home`), `compare`; web `apps/web/src/pages/system/Restore.tsx`, `apps/web/src/test/restore.test.tsx`; `test_ws_backup_api.py::test_restore_via_upload_verify_apply_and_compare` | — | — |
| Credential-heraankoppeling | **Onvolledig** | Herstelcontrole meldt of sleutels passen (`credentials`), `shc-api rekey --old-key-file` (CLI), opnieuw koppelen herstelt (`repaired: true`); `test_ws_backup_restore.py::test_rekey_reencrypts_credentials_and_secret_settings`, `test_gapfix_keys.py::test_restore_key_check_reports_encrypted_settings_of_another_key`, `test_ws_devices_api.py::test_repair_reuses_integration_and_moves_devices` | P3-11: oude hoofdsleutel uploaden + rekey via de web-app (web-admin, eenmalig, niet bewaard) — zonder terminal | Hoofdsleutel nooit in een back-up of in de Hub |
| Scherm: verhuisassistent met dry-run en dubbele-apparaatdetectie | **Bestaand** / detectie **Onvolledig** | Assistent: Systeem › Herstellen en verhuizen; dubbel apparaat bij koppelen herkend op serienummer/MAC/bridge-id (`docs/api.md:500-504`) | `restore/compare` toont dubbele apparaten (zelfde `stable_identity` in oude én nieuwe woning) expliciet (P3-11, klein) | — |
| Acceptatie: **verhuizing naar mock-NAS behoudt zones/historie** | **Onvolledig** | Herstel in tijdelijke database: `test_ws_backup_restore.py::test_restore_drill_compares_counts_and_checks_the_current_key`, `::test_restore_as_new_home_archives_the_old_home_and_switches_its_integrations_off`, `test_ws_followups.py::test_restore_new_home_copies_profiles_and_schedules_as_drafts` | P3-01/P3-10: E2E met twee alles-in-één containers (back-up op A, herstel op B met ander IP/datamap) — EX50 | — |
| Acceptatie: **nieuw IP vereist geen datareset** | **Bestaand** | herkenning op identiteit; `PATCH /integrations/{id}` `{host, verify:true}` (`test_ws_devices_api.py::test_patch_integration_address_checks_identity`); thuisadres via `PUT /system/settings` | Telefoons met oud thuisadres: opnieuw koppelen (gedocumenteerd) | — |

### B06-009 — Hardwarecontrole (F4, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Zigbee USB, Hue, Gree, HomeWizard echt testen; Android QR push + widget | **Geblokkeerd** | Checklist `docs/testlog.md` §"Eerste test op je NAS" (9 stappen) en §"Nog niet getest"; nep-apparaten in CI | Uitvoeren op echte hardware (D12–D14, D13 dongle mogelijk nog niet gekocht) | Geen "getest" zonder echte run |
| Installatie stack 25.10-compat; reboot | **Geblokkeerd** + **Ontbrekend** (installatiewijze) | Compose-stack in CI (`stack`-job) | Eerst P3-01 (alles-in-één Custom App), daarna test op de echte TrueNAS-versie (D01) incl. reboot | Niet testen via *Install via YAML* (contract 1.1) |
| "Full docker compose" | **Strijdig met contract 1.1** | — | Gelezen als: volledige alles-in-één Custom App (ADR-0003 punt 8) | Compose niet als installatiemethode |
| Cloudflared access scope | **Onvolledig** | `docs/buiten-huis.md`, `compose.yaml:222-242`, nginx vertrouwt tunnel alleen op vast adres | In de alles-in-één image eerst uit (ADR-0004 punt 5/7); eigenaar netwerk (B10) | — |
| Scherm: testlog met echte apparaatmodellen | **Onvolledig** | `docs/testlog.md` (alleen sim); apparaatkaart met `tested_at` | Testlog per apparaat (P3-07) | — |
| Acceptatie: **geen productie-approved status vóór echte device tests** | **Bestaand** (houding) | Alles staat op "nog niet getest" (`docs/compatibility.md:3`, release-notes) | Manifest `validation_status: "NIET GEVALIDEERD (echte NAS)"` (P3-01) | Nooit `Gereed` zetten |

---

## 2. Algemene B06-eisen (§1–§3, §5–§7 van het bouwplan)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging |
|---|---|---|---|
| §1 Schermen: Woningoverzicht, Kamer, Klimaat, Lampen, Energie, Apparaten, Sensoren, Meldingen, Aanpassen/verhuizen | **Bestaand** (eigen web-app + Android) | `apps/web/src/App.tsx:45-54` (Overzicht, Klimaat, Verlichting, Energie, Apparaten, Systeem › Herstellen en verhuizen); kamers via zones/`GET /homes/{id}/rooms`; sensoren op Apparaten + wizard *Sensoren toewijzen*; meldingen op Overzicht + Android | Geen aparte "Kamer"- of "Meldingen"-pagina (meldingen staan op Overzicht); niet nodig voor B06 (laag) |
| §1 Hub-aggregaatkaart | **N.v.t.** (eigenaar `ai-server-manager`) | — | my_house levert connector-data (P3-03) |
| §1 Per scherm: responsief 360/390/768/1440 | **Onvolledig** | Playwright 360 + 320 px (`apps/web/e2e/10-mobile.e2e.ts:57-59`) en standaard desktop | P3-13: 390/768/1440 aan de Playwright-matrix toevoegen |
| §1 Leeg/laden/storing/offline, NL-fouten, toetsenbord, auditspoor, laatste update, bronherkomst | **Bestaand** | `apps/web/e2e/08-offline.e2e.ts`, vitest-toegankelijkheidstests (`docs/testlog.md` §Wat de suites dekken), Account › Logboek (`GET /audit`), `Measurement.at/quality` | — |
| §1 Zoek/filter/paginering | **Onvolledig** | Logboek met `before`-cursor (`routers/users.py:202`); apparaten zonder filter/paginering | Laag; alleen op connector-`items` verplicht (P3-03) |
| §2 Datamodel blijft in my_house; Hub alleen cache/metadata | **Bestaand** | `INVENTARIS.md` §2.3 | — |
| §2 Forward-schema-tests bij migraties | **Bestaand** | `test_ws_backup_restore.py::test_older_backup_is_restored_in_its_schema_then_migrated_and_drops_are_listed`, `::test_backup_of_newer_schema_is_refused`, `test_gapfix_security.py::test_migration_0004_limits_legacy_phone_tokens` | CI-stap `alembic upgrade head → downgrade base → upgrade head` (P3-10) |
| §2 Tijdreeksen met retentie/aggregatie | **Bestaand** | `energy_hourly`, `GET/PUT /system/retention`, `test_gapfix_operations.py::test_zone_log_retention_follows_the_app_setting` | — |
| §2 Stabiele ID, versie, eigenaar, timestamps, provenance | **Onvolledig** | UUID's, `created_at`/`updated_at` op de meeste tabellen, `stable_identity`, `quality` | `version`/ETag ontbreekt (P3-05 voor zones/lichtgroepen) |
| §2 Secrets alleen als vault-referentie | **Bestaand** (eigen kluis) | AES-GCM-versleuteld (`security.py:263-284`), nooit in diagnose/back-up-redactie | — |
| §3 Bestaande routes `/homes/{id}/overview`, `/zones/{id}`, `/zones/{id}/command` vergelijken met Swagger/live | **Bestaand** (code) / **Geblokkeerd** (live) | OpenAPI-export van HEAD `docs/openapi/my_house.openapi.json` (91 paden, 116 operaties) komt overeen met `docs/api.md`; live versie op de NAS onbekend (D07) | OpenAPI-driftcontrole in CI (P3-10); live vergelijking door de Hub-adapter |
| §3 Auth, rol, validatie, idempotency, cursors, rate limits, time-outs, capability-detectie, machineleesbare fouten | **Onvolledig** | auth/rol/validatie/rate limits/foutcodes bestaan; idempotency, cursors en capability-detectie niet | Alleen op connector-routes (ADR-0003 punt 3–5) |
| §5 Hardware niet als aanwezig beschouwen; onafhankelijk van Home Assistant; PWA + Android | **Bestaand** | `README.md`, preflight meldt ontbrekende dongle | — |
| §6 Oplevering: Compose/TrueNAS-manifest met gepinde images, `.env.example`, OpenAPI/connector-schema's, restore-bewijs, release-notes, handleiding, screenshots | **Onvolledig** | release-notes, handleidingen, restore-tests (sim) | P3-01 (manifest, CUSTOM_APP.md, `.env.example`), P3-02 (digests), P3-03 (connector-schema's), screenshots web/mobiel van de echte relevante schermen (P3-13) |
| §7 Proof of done: veilig degraderen bij stroomuitval, uitval afhankelijke app, NAS-herstart, gewijzigde credentials, incompatibele versie; geen directe writes in DB van een andere app | **Onvolledig** | worker-/DB-/MQTT-uitval (sim), ingetrokken token → 401, nieuwer back-upschema geweigerd; my_house schrijft nergens anders | Incompatibele versie (oudere image + nieuwer schema) → nette weigering (P3-09); NAS-herstart **Geblokkeerd** |

## 3. Installatie- en bestaande-app-stappen (my_house-kant)

| ID | Stap (I05/A20) | Klasse | Bewijs / toelichting | Wat mist |
|---|---|---|---|---|
| I05-05-01 | Dataset en repository; `install.sh` voor geheimen | **Strijdig met contract 1.1** | `infra/scripts/install.sh` vraagt root via shell | P3-01: first-start-init in de image maakt geheimen in `/appdata/secrets`; formulier vraagt alleen image/tag/poort/opslag |
| I05-05-02 | YAML via `render_truenas.py` | **Strijdig** | `infra/scripts/render_truenas.py` | Vervalt (alleen nog CI/ontwikkeling tot P3-01 klaar is) |
| I05-05-03 | *Install via YAML* | **Strijdig** | `docs/installatie-truenas.md:75` | `deploy/truenas/CUSTOM_APP.md` (standaardformulier) |
| I05-05-04 | Hue-koppeling + readback | **Bestaand** (sim) / **Geblokkeerd** | wizard + `POST /integrations/hue/pair`; `test_hue.py` | Echte bridge |
| I05-05-05 | HomeWizard-rollen; boiler zonder write | **Bestaand** (sim) / **Geblokkeerd** | rollen + boilerblokkade (§1 B06-003/005) | Echte meters (D14) |
| I05-05-06 | Gree-compatibiliteit, conflict, remote-off | **Bestaand** (sim) / **Geblokkeerd** | §1 B06-004 | Echte units (D12) + ADR-0005-gate |
| I05-05-07 | Zigbee optioneel (`ls -l /dev/serial/by-id/`, compose-flag) | **Onvolledig** / **Geblokkeerd** | compose-profiel `zigbee` | Shell-commando en compose-flag vervallen; dongle via formulier (ADR-0004 punt 6); dongle (D13) |
| I05-05-08 | Android + restore op tweede stack | **Bestaand** (sim) / **Geblokkeerd** | APK v0.1.0 in release; restore-tests | 0.2.0-APK niet uitgebracht (ADR-0001); tweede stack = EX50 (P3-10) |
| A20-A0 | Meten: image-tag/digest, poorten, datasets, afhankelijkheden, gebruik | **Geblokkeerd** | Op de NAS onbekend (D01/D02/D03/D07); repo-kant in `INVENTARIS.md` | Hub-onboarding meet dit via TrueNAS-API (read-only) |
| A20-A1 | Niet-mutatief integreren: catalogusitem, status, deep-link | **Onvolledig** | `/api/v1/health` (public) + versie | P3-03 `discover`/`health` + manifest |
| A20-A2 | Alleen ontbrekende functies via de data-eigenaar | — | deze matrix | §9 |
| A20-A3/A4 | Proefstop / behoud | **N.v.t.** (geen kandidaat voor vervanging) | — | — |

## 4. Integrale E2E-scenario's met een my_house-kant

| ID | Scenario | Klasse (my_house) | Bewijs | Wat mist |
|---|---|---|---|---|
| EX24 | Gree offline → geen nieuwe commando's, duidelijk stale | **Bestaand** (sim) | 503 `device_offline` bij commando (`commanding.py:148-149`); `test_core_logic.py::test_offline_unit_does_nothing`, `test_ws_climate_api.py::test_offline_unit_is_a_warning_not_a_missing_unit`, `test_gree.py::test_offline_unit_raises_after_bounded_retries` | Connector-health `degraded` + event (P3-03/04) |
| EX25 | Gedeelde buitenunit A heat, B cool → geweigerd met reden | **Bestaand** (sim) | §1 B06-004 (409 + `denied`) | Ook via connector (P3-05) |
| EX26 | Afstandsbediening OFF → blijft uit tot handmatig hervatten | **Bestaand** (sim) | `test_core_logic.py::test_remote_off_is_never_reactivated`, `test_ws_climate_engine.py::test_remote_change_during_off_becomes_manual` | — |
| EX27 | Boiler: UI/Hub/AI/HTTP → alles geblokkeerd, alleen lezen | **Onvolledig** | §1 B06-003 | P3-06: één regressietest over alle routes incl. connector + AI-actor; 409 `policy_denied` overal |
| EX28 | P1-meterreset + zomertijd → totalen kloppen, label transparant | **Bestaand** (sim) | §1 B06-005 | — |
| EX29 | Zigbee-sensor stale → veilige terugval + melding na time-out | **Onvolledig** | terugval getest; melding `worker/health.py:231-263` ongetest | P3-08 |
| EX30 | Gree-firmware incompatibel → unsupported, nooit pseudo-bediening | **Bestaand** (sim) | `test_gree_bind.py::test_bind_failure_is_unsupported_with_scan_diagnostics`, `test_ws_devices_api.py::test_pair_failure_keeps_diagnostics_and_maps_errors` | — |
| EX50 | Migratie naar andere host → datalinks logisch na IP-wissel | **Onvolledig** | §1 B06-008 | P3-10 (twee alles-in-één containers) |
| EX15 | DB-schema-rollback: oude image + nieuwer schema → tag-only rollback geblokkeerd | **Onvolledig** | nieuwer back-upschema geweigerd; oude image start niet (alembic-fout) | P3-09: nette startweigering + herstelpagina |
| EX16 | Credential ingetrokken → geen open toegang | **Bestaand** | 401 `token_revoked` | — |
| EX17/EX18/EX45 | Prompt-injectie / klasse-vervalsing / Node-RED-flow vraagt verboden actie | **Onvolledig** (my_house-kant) | my_house-beleid is onafhankelijk van de aanvrager | P3-05/06: connector negeert `risk_claim`, allowlist, boiler altijd `policy_denied` |
| EX20 | Approval replay → één uitvoering | **Ontbrekend** | — | P3-05: nonce-tabel + `Idempotency-Key` |
| EX12 | Back-up stale → hoge prioriteit | **Bestaand** (bron) | `watch.py` + `backup_check` (>36 h), push | Event `backup.run.failed` naar de Hub (P3-04) |

## 5. Conformiteit met `PLATFORM_CONTRACTEN.md` 1.1

| § | Eis | my_house 0.2.0 | Actie |
|---|---|---|---|
| 1 | Standaard Custom App, geen YAML/compose/terminal | **Nee**: `install.sh` + *Install via YAML* | P3-01 |
| 1 | Eén alles-in-één image, PostgreSQL via unix-socket, `/appdata`, één poort, 568 | **Nee**: 5–7 containers, uids 10001/70/1883/101/1000 | P3-01 |
| 1/10 | Geen `latest`; base-images op `@sha256` | **Nee**: `release.yml:103,118` pusht `:latest`, compose-fallback `:latest`, base-images op tag | P3-02 |
| 1 | Geheimen door de app zelf in `/appdata/secrets` | **Deels**: via `install.sh` | P3-01 |
| 1 | UI-taal NL; waarde + eenheid + bron + laatst bijgewerkt; onbekend ≠ groen | **Ja** (`Measurement`, `Check.state: unknown`) | — |
| 2 | Rij in de app-tabel | **Ontbreekt** in het contract | ADR-0003 punt 1 (contract_issue) |
| 3 | Enrollment / koppelcode voor de Hub | **Deels** (koppelcode bestaat voor telefoons) | P3-04 |
| 4 | Diensten zonder geldige auth → 401 `{code:"unauthorized"}` | **Deels**: 401 `auth_required`/`token_revoked` in `{"detail":…}` | Connector-routes volgen het contract (ADR-0003 punt 3) |
| 5 | `/healthz`, `/readyz` zonder auth | **Deels**: `/api/v1/health`, nginx `/healthz` | P3-03 |
| 5 | Foutformaat, `Idempotency-Key`, ETag, cursor | **Nee** | Op connector-routes (ADR-0003) |
| 5 | OpenAPI-export `docs/openapi/<dienst>.openapi.json`, CI-drift | **Momentopname toegevoegd in P0**, driftcontrole ontbreekt | P3-10 |
| 6 | Connectorcontract | **Nee** | P3-03, P3-05 |
| 7 | Event-envelope, outbox | **Nee** | P3-04 |
| 8 | Ondertekend `ExecutionRequest`, boiler altijd geblokkeerd | Boiler **ja**; handtekening **nee** | P3-05, P3-06 |
| 9 | Manifest | **Nee** | P3-01 |
| 10 | `deploy/allinone`, `deploy/truenas/CUSTOM_APP.md`, `deploy/dev`, `deploy/manifest`, `.env.example`, `docs/adr`, CHANGELOG, Makefile | **Nee** (ADR's toegevoegd in P0) | P3-01/P3-02 |
| 11 | EVIDENCE.json | **Toegevoegd in P0** | — |
| 12 | Bestaande integriteit/rollback/monitoring niet dubbel/uitschakelen | — | §10 |

Overige punten zonder terminal (P3-11): updaten/terugdraaien (`docs/onderhoud.md:13-42` vraagt `docker exec … psql`),
`reset-password`, `rekey`, `restore-drill`, `maintenance` (alleen CLI). Handmatige noodstop via UI/API ontbreekt
(nu alleen veilige modus na herstel en onderhoudsmodus via CLI) → P3-11: `POST /system/safe-mode/enable` (admin,
geauditeerd) en connector-actie `home.commands.pause` (R1, alleen *aanzetten*; opheffen alleen door een beheerder in
my_house).

## 6. Hub-adapter: welke bestaande routes de Hub kan gebruiken (connectorcontract §6, enrollment §3)

**Koppelen (bestaat):** gebruiker maakt in my_house een koppelcode met rol `viewer` (`POST /api/v1/pairing/codes`,
web-admin, vereist een thuisadres) → Hub wisselt in met `POST /api/v1/pairing/redeem {code, device_name}` → token
`shc_…` (180 dagen; vernieuwen met `POST /api/v1/clients/self/refresh`; intrekken in my_house). Serverherkenning:
`GET /api/v1/pairing/identity?nonce=&client_id=`.

| Hub-behoefte | Bestaande route (viewer-token voldoende) | Gebruiken? |
|---|---|---|
| App leeft + versie | `GET /api/v1/health` (public) | ja |
| Gezondheid/afhankelijkheden | `GET /api/v1/system/status` (database, worker, schijf, back-up, klok, veilige modus, onderhoud, herstel, koppelingen) | ja (tot `/connector/v1/health` er is) |
| Woningen + huiskaart | `GET /api/v1/homes`, `GET /api/v1/homes/{id}/overview` | ja |
| Zones/klimaat | `GET /api/v1/homes/{id}/zones`, `GET /api/v1/zones/{id}`, `…/history`, `…/log`, `…/schedule`, `GET /api/v1/homes/{id}/climate/roles` | ja |
| Licht | `GET /api/v1/homes/{id}/lights`, `…/buttons` | ja |
| Energie (B14) | `GET /api/v1/homes/{id}/energy/summary`, `…/energy/live`, `…/energy/export?format=json`, `…/tariffs`, `GET /api/v1/energy/limits` | ja |
| Meldingen | `GET /api/v1/homes/{id}/alerts` | ja (polling; max. 100 open) |
| Apparaten/compatibiliteit | `GET /api/v1/homes/{id}/devices`, `GET /api/v1/devices/{id}`, `GET /api/v1/integrations`, `…/compatibility` | ja |
| Realtime | `WS /api/v1/ws?home_id=` (eerste bericht `{"type":"auth","token":…}`) | optioneel (geen replay) |
| Deep-link | `GET /api/v1/system/settings` (`lan_url`, `remote_url`) | ja |

**Niet voor de Hub** (te hoog niveau of bewust uitgesloten): `/audit`, `/devices/{id}/diagnostics`,
`/integrations/{id}` (admin); back-ups/herstel/gebruikers/koppelcodes (web-admin); **alle `control`-routes**
(`POST /zones/{id}/command`, `/zones/{id}/resume`, `/lights/groups/{id}`, `/alerts/{id}/ack`) — de Hub bedient alleen
via P3-05.

**Echt ontbrekend** (P3): `/connector/v1/discover`, `/health`, `/items`, `/events?cursor=`, `/evidence/{id}`,
`/propose`, `/apply`, `/rollback` (→ 501), `/healthz` + `/readyz` op API-niveau, dienst-clienttype, outbox,
Ed25519-verificatie van `ExecutionRequest`, nonce- en idempotency-tabellen, back-upstatus als item voor B12
(laatste geslaagde back-up, sha256, laatste herstelproef — nu alleen web-admin of CLI), enrollment via `HUB_URL`
(alleen nodig als de Hub my_house via de API installeert; lager).

## 7. Custom App-controle (alles-in-één)

| Vraag | Antwoord |
|---|---|
| Is my_house nu als standaard TrueNAS Custom App met één image te installeren? | **Nee.** Er bestaan alleen losse images `-api` en `-web` plus derde-partij-images; installatie vraagt `install.sh` (root) en een compose-YAML (*Install via YAML*). |
| Heeft het compose nodig? | **Ja**, nu wel (db, api, worker, web, mosquitto + optioneel zigbee2mqtt/cloudflared, drie netwerken). Dat is het grootste P3-hiaat. |
| Wat bestaat al dat hergebruikt kan worden? | Hashed lock + Dockerfile-stappen, nginx-config, pre-migratiedump, liveness-bestand + waakhond, rooktest `infra/scripts/smoke_test.py`, browsertests `infra/e2e`, back-up/herstel voor de migratie van bestaande installaties. |
| Open punten | Zigbee2MQTT in of naast de image; apparaat-doorgifte in het formulier (D01); tunnel; vertrouwensgrenzen binnen één container (ADR-0004). |

## 8. Releasestatus — wat ontbreekt voor een echte release (zonder zelf te taggen of te publiceren)

1. Release-run 0.2.0 opnieuw laten draaien door de eigenaar (faalde op Gradle-download 504) — of 0.3.0 met P3-werk.
2. Geen `:latest`; base-image-digests; `release/digests.json`; GHCR-pakket openbaar maken (nu 401 anoniem).
3. Alles-in-één image (P3-01) + CI-start/healthcheck.
4. Hardwareacceptatie (B06-009) — **Geblokkeerd**.

## 9. Geprioriteerde bouwlijst fase P3 (alleen hiaten)

| Nr | Prio | Wat (module · route · migratie) | Dekt | Niet doen |
|---|---|---|---|---|
| **P3-01** | P0 | `deploy/allinone/` (Dockerfile op digests, `supervisor.py`, `healthcheck.py`, first-start-init), `deploy/truenas/CUSTOM_APP.md`, `deploy/manifest/my_house.manifest.yaml`, `.env.example`, `deploy/dev/compose.yaml`; CI-job die de image bouwt en start (root én 568), rooktest + browsertests tegen die container; migratiepad bestaande installatie via back-up/herstel in de UI | B06-009, I05-05-01…03/07, contract §1/§9/§10, ADR-0004 | Geen YAML/compose als installatie; geen Docker-socket; geheimen nooit overschrijven |
| **P3-02** | P0 | `release.yml`/`edge.yml`: `:latest` weg, digests in `release/digests.json`; Dockerfiles en dev-compose op `@sha256`; Dependabot blijft | contract §1 | Zelf geen tag/release publiceren |
| **P3-03** | P0 | `connector/` (nieuwe router) — `GET /connector/v1/discover`, `/health`, `/items?type=home\|zone\|light_group\|device\|alert\|energy_total\|backup_status&cursor=&limit=`, `/evidence/{id}`; `GET /healthz`, `GET /readyz`; contractfoutformaat; lees-modellen = bestaande `views/*` | B06-006, B06-005 (Hub-kant), A20-A1 | Geen tweede berekening; geen per-binnenunit-verbruik |
| **P3-04** | P0 | Dienstidentiteit: `api_clients.kind` (`phone\|service`, migratie **0005** met downgrade); `service` maximaal `viewer` op gewone routes; outbox `connector_outbox` (migratie 0005) gevuld bij `events.publish`/`raise_alert`/`resolve_alert`/uurtotaal; `GET /connector/v1/events?cursor=`; optioneel push naar Hub-inbox na enrollment | B06-006, EX12 | Geen exact-once; geen geheimen in events |
| **P3-05** | P0 | `POST /connector/v1/propose` + `/apply` + `/rollback` (501): Ed25519-verificatie (publieke policy-sleutel uit de koppeling), vervaltijd, nonce (`connector_nonces`), `Idempotency-Key` (`idempotency_keys`), ETag op zone/lichtgroep, allowlist (`light.group.set`, `light.scene.recall`, `zone.command`, `zone.resume`, `zone.schedule.propose`), aanroep van `request_zone_command`/`queue_command`; evidence met before/after + readback | B06-006, B06-007, EX17/18/20/45 | Geen eigen beleid naast `policy.py`; `risk_claim` nooit gebruiken |
| **P3-06** | P0 | Boiler-regressie EX27: 409 `policy_denied` voor elk beschermd doel op lichtroute, zone-route, knopkoppeling, connector (ook `actor.type=ai`); `PATCH /zones/{id}` alleen `ac_unit` als unit | B06-003, EX27 | Bestaande drie lagen niet vervangen |
| **P3-07** | P0 | Inbedrijfstellingsgate (ADR-0005): `check_command` weigert `untested` apparaten; wizardvolgorde meters → Hue lezen → Hue test → Gree na D12; testlog per apparaat; matrix uitbreiden (protocolversie, detectie, terugval) | B06-001, B06-002, B06-009 | Niet automatisch `tested` zetten |
| **P3-08** | P1 | Test voor sensorveroudering-melding (`worker/health.py:231-263`) | B06-004, EX29 | — |
| **P3-09** | P1 | Startweigering bij nieuwer schema (oudere image): API start in herstelmodus met NL-pagina i.p.v. crashlus; geen migratie | EX15, §7 Proof of done | Nooit automatisch downgraden |
| **P3-10** | P1 | CI: OpenAPI-driftcontrole (`docs/openapi/my_house.openapi.json`), alembic up/down/up, E2E twee alles-in-één containers (back-up A → herstel B, ander IP) | EX50, B06-008, contract §5 | — |
| **P3-11** | P1 | Zonder terminal: oude hoofdsleutel uploaden + rekey (web-admin), herstelcode voor "wachtwoord vergeten" (eenmalig, in app-logs zoals de Hub-setupcode), herstelproef starten vanuit de UI, handmatige noodstop `POST /system/safe-mode/enable`; `restore/compare` toont dubbele apparaten | B06-008, contract §1 | Herstel blijft via de eigenaar (my_house), niet via de Hub-DB |
| **P3-12** | P2 | Flaky `test_push_stream_batches_and_restarts` begrenzen en oorzaak vinden | B06-002 | Test niet uitzetten |
| **P3-13** | P2 | Playwright 390/768/1440; screenshots web/mobiel voor de oplevering | B06 §1/§6 | — |
| **P3-14** | P2 | Huishouding: gecommitte `shellcheck_py-…whl` verwijderen, `docs/testlog.md` naar 0.2.0, README/installatiedocs naar Custom App, CHANGELOG | — | — |

## 10. Wat in P3 nooit mag (samengevat)

- De boilerblokkade (`policy.py`, worker-hercontrole, `relay_allowed=False`) verzwakken, omzeilen of dupliceren.
- Een klimaat-/Gree-regelaar of energieberekening in de Hub bouwen; my_house blijft eigenaar en enige schrijver.
- De Hub een `control`- of `admin`-token geven, of AI rechtstreeks de bestaande bedieningsroutes laten gebruiken.
- "Bevestigd/gelukt" melden zonder readback; een ontbrekende functie simuleren in plaats van `capability_unavailable`.
- Back-up/herstel, pre-migratiedump, veilige modus, waakhond of `StatusWatch` vervangen of uitschakelen.
- Installatie via YAML/compose/shell aanbieden; `latest`-tags; geheimen in het formulier, Git of logs.
- Apparaten automatisch op `tested` zetten of iets `Gereed`/`Geslaagd` noemen zonder echte hardware-/NAS-test.
