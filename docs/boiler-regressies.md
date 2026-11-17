# Boilerregressies — S-P3-705

De bestaande my_house-ingangen zijn lokaal met tijdelijke PostgreSQL-databases, ASGI-clients,
adapterevents en nep-adapters beproefd. Er is niets tegen echte NAS, Emby, apparaten of accounts
uitgevoerd. B06-003 blijft **Bezig**, met de bestaande **Unit-getest; Sim-getest;
NIET GEVALIDEERD (hardware)**. EX27 bevat uitsluitend het werkelijk gedraaide my_house-deel en
blijft **Bezig; Sim-geslaagd; NIET GEVALIDEERD (hardware)**. Ondertekende connector, Hub-/AI-integratie
en fysieke acceptatie staan nog open; er rest dus meer dan alleen een NAS-test.

## Ingangen en observeerbaar bewijs

| Ingang | Proef en waarneming |
|---|---|
| Zone-PATCH | `test_boiler_routes.py`: beschermd doel geeft 409 `policy_denied` vóór soortvalidatie; verkeerde unit-/sensorsoort geeft 422. Alle velden blijven bij weigering gelijk. Geldige airco + Zigbee-sensor en ontkoppelen werken. |
| Zonecommando: modus, temperatuur, ventilator | `test_boiler_lights.py::test_zonecommando_bescherming_voor_offline`: online én offline beschermd doel geeft 409 met de juiste code, lege wachtrij. |
| Lichtroute en scènebody | `test_lichtroute_beschermd_pad_en_body`: boiler-id, beschermde lichtgroep, beschermde groep met gewone scène en gewone groep met beschermd/bodydoel; geen wachtrijregel. |
| Knop POST, PUT en PATCH | `test_knopopslag_weigert_beschermd_doel_atomair`: licht-, scène-, klimaat- en hervatdoel. Geen gedeeltelijke vervanging van bestaande mappings. |
| Knopsimulatie | `test_boiler_ketens.py::test_knopsimulatie_beschermde_oude_koppeling`: bestaande/importkoppelingen bereiken de simulatie, per schakelstap `refused/policy_denied`, geen wachtrij. Hervatten meldt uitsluitend een configuratiewijziging (`would_run`). |
| Knopdruk en replay | `test_echte_knopdruk_en_replay`: echte `ButtonEvent` via `Worker.handle_events`; één opgeslagen `ProcessedEvent` na replay, bijgewerkte knopstatus, licht/scène/klimaat/hervatten/gecombineerd, geen adapteruitvoering. Bij hervatten loopt ook de klimaatronde tot `policy_denied`. |
| Schema, beleid, hervatten, profiel en profielwijziging | `test_automatisering_tot_aan_adapter`: elke echte configuratieroute geeft 200; de regelaar wil aantoonbaar sturen (`last_decision` eindigt op `policy_denied`), maar maakt geen commando en roept de adapter niet aan. |
| Planner en overgangen | Dezelfde proef controleert een werkelijk toegepaste actieve variant. `test_planner_overgangen_met_profielherstel` doorloopt pauzeren, hervatten, stoppen en verwijderen; stoppen/verwijderen herstellen aantoonbaar het eerdere profiel, waarna de klimaatronde de boiler weigert. |
| Adaptief fit/activate/disable | Werkelijke fit uit 110 opgeslagen kwartiermetingen, betrouwbaar advies geactiveerd, klimaatronde bereikt de commandogrens, geen uitvoering, daarna uitschakelen. |
| Interne commandokern | `test_interne_commandokern_weigert_elke_bron_en_actie`: user/button/engine × set_relay/set_power/switch/set_group/set_hvac/recall_scene, geen wachtrij. |
| Wachtrij, rolwijziging en nieuwe poging | `test_worker_hercontrole_na_rolwijziging`: echte HTTP-, knop- of engine-ingang maakt eerst één toegestaan commando; daarna wordt het doel beschermd. Ook met een reeds begonnen poging: `denied`, nul adapteraanroepen. `test_worker_actiematrix_via_interne_commandokern` dekt daarnaast zes acties × drie bronnen via de echte interne queuefunctie. Geen rechtstreeks ingevoegde wachtrijregel als routebewijs. |
| Import en vrijgave | `test_import_vrijgave_en_oude_knop_tot_adapter`: web verify/apply en CLI-herstel zelf, beide met v1 én v2. Een via HTTP aangemaakt commando en een oude knopmapping zijn echt teruggezet; veilige modus is eerst aan, vervolgens via de route vrijgegeven. De herstelde knopdruk en worker blijven geblokkeerd; nul uitvoering. |
| Binden/ontkoppelen/vervangen/archiveren/getest | `test_apparaatlevensloop_en_oude_relaiskoppeling`: echte beheerroutes, bij vervangen aantoonbaar herschreven doel in de oude mapping. Ook na ontkoppelen en heractiveren blijft de Energy Socket alleen meten. Knopdruk bereikt ingest, geen schakeling. |
| Ontdekken/koppelen/adres/probe/diagnostiek | `test_ontdekken_koppelen_adres_diagnose_schakelen_nooit`: complete HTTP-keten met geïnstrumenteerde nep-provider. Discover, pair, probe en health zijn daadwerkelijk aangeroepen; execute nooit. |
| Productiehook HomeWizard | `test_productie_homewizard_hook_blijft_dicht`: echte HomeWizard-adapter via zowel `routers/devices._adapter_for` als `Worker.start_integration`; beide relaisstanden geweigerd, nul gemockte PUT's. De opstarttaken van deze eigen test doen geen netwerkwerk en worden afgewacht. Bestaande negen HomeWizard-tests blijven groen. Een positieve controle bewijst dat dezelfde HTTP-testhulp een toegestaan fictief doel werkelijk tot de gemockte PUT brengt. |
| WebSocket | `test_websocket_heeft_geen_commandodispatch`: geldige beheersessie, hello bevestigt bedieningsrechten, vier commandoberichten gevolgd door ping/pong. Berichten zijn dus verwerkt; geen wachtrij- of adaptermutatie. |
| Rechtenmatrix | `test_volledige_rechtenmatrix`: web admin, telefoon admin/control/viewer en web viewer; geldige sessie met CSRF of werkelijk ingewisseld koppeltoken. Alle acht negatieve routegevallen, plus een positief lichtcommando. Configuratie blijft admin; viewer/control krijgen waar nodig 403, bevoegde aanvragers krijgen de boilerweigering 409. |
| Android | Zoekopdracht op `setGroup`, `sendLightCommand`, `404` en `policy_denied` in `ApiClient.kt` en `HomeRepository.kt`. `sendLightCommand` vangt fouten generiek; 404 komt alleen voor als algemene statusvertaling. Geen afhankelijkheid van licht-404 gevonden. Geen Android-uitvoering of echte clientinterop geclaimd. |

## Reproduceren

Vanuit de repomap, met de lokale testdatabasevoorziening:

```sh
cd apps/api
PATH=/usr/lib/postgresql/16/bin:$PATH SHC_TEST_PG=postgresql://postgres@127.0.0.1:5432 \
  nice -n 10 ../../.venv/bin/python -m pytest -q \
  tests/test_boiler_routes.py tests/test_boiler_lights.py tests/test_boiler_ketens.py
```

Vanuit de repomap, voor alle mutaties (of met `--weg` voor één ingang):

```sh
PATH=/usr/lib/postgresql/16/bin:$PATH SHC_TEST_PG=postgresql://postgres@127.0.0.1:5432 \
  nice -n 10 .venv/bin/python tools/boiler_mutaties.py --uitvoer /tmp/boiler-mutaties
```

De mutatietool wijzigt geen bronbestand. Hij laadt per ingang een tijdelijke pytest-plugin in een
apart proces en neemt exitcode plus letterlijke slotregel over. Alleen een falende testassertie telt;
collectie- en setupfouten gelden als onbewezen. De normale tests gebruiken de ongewijzigde productiecode.
Bij WebSocket en de leesroutes is er geen schakelcontrole om weg te halen: daar introduceert de
mutatie opzettelijk verboden dispatch na de werkelijk aangeroepen ingang. De droge hervatsimulatie
belooft geen uitvoering; de echte hervatketen heeft wel een beleidsmutatie.

## Grens en resterend werk

`policy.check_command`, de workerhercontrole en de productiehooks `relay_allowed=False` zijn behouden.
De routervalidatie roept hetzelfde beleid aan; er is geen tweede boilerbeleid gebouwd.
Ondertekende connector-/AI-actortests volgen bij S-P3-740/742 en Hub S-P3-130. S-P3-710 (ongetest
apparaat), S-P3-715 (algemene TTL/herstart), S-P3-720/730 (image/herstelbewaker) en de fysieke acceptatie
S-P6-21 vallen buiten deze stap. Er is geen releasetag, contractwijziging, UI-wijziging of Excel-mutatie.

## Gemeten mutaties (2026-11-17)

55 verschillende mutaties gedood; elke hieronder genoemde pytest-aanroep gaf exitcode 1.
De overkoepelende mutatietool gaf exitcode 0. De slotregels zijn letterlijk overgenomen.

| Weg | Letterlijke pytest-slotregel |
|---|---|
| `zone-patch` | `6 failed, 2 passed in 4.85s` |
| `zone-typen` | `2 failed in 6.29s` |
| `zone-command` | `6 failed in 3.30s` |
| `licht-pad-body` | `5 failed in 2.05s` |
| `knop-post` | `1 failed in 2.06s` |
| `knop-put` | `1 failed in 2.38s` |
| `knop-patch` | `1 failed in 2.31s` |
| `knopdruk-licht` | `1 failed in 2.13s` |
| `knopdruk-scene` | `1 failed in 2.49s` |
| `knopdruk-zone` | `1 failed in 2.23s` |
| `knopdruk-hervatten` | `1 failed in 2.13s` |
| `knopdruk-gecombineerd` | `1 failed in 2.38s` |
| `simulatie-licht` | `1 failed in 2.05s` |
| `simulatie-scene` | `1 failed in 2.46s` |
| `simulatie-zone` | `1 failed in 2.29s` |
| `automatisering-schema` | `1 failed in 2.86s` |
| `automatisering-beleid` | `1 failed in 2.46s` |
| `automatisering-hervatten` | `1 failed in 2.71s` |
| `automatisering-profiel` | `1 failed in 2.80s` |
| `automatisering-profielwijziging` | `1 failed in 2.83s` |
| `automatisering-planner` | `1 failed in 2.83s` |
| `automatisering-adaptief` | `1 failed in 3.25s` |
| `planner-pauzeren` | `1 failed in 2.93s` |
| `planner-hervatten` | `1 failed in 2.74s` |
| `planner-stoppen` | `1 failed in 2.90s` |
| `planner-verwijderen` | `1 failed in 2.78s` |
| `worker-user-False` | `1 failed in 2.71s` |
| `worker-user-True` | `1 failed in 2.91s` |
| `worker-button-False` | `1 failed in 3.04s` |
| `worker-button-True` | `1 failed in 3.03s` |
| `worker-engine-False` | `1 failed in 3.17s` |
| `worker-engine-True` | `1 failed in 3.12s` |
| `rechten-web-admin` | `1 failed in 2.89s` |
| `rechten-phone-admin` | `1 failed in 2.83s` |
| `rechten-phone-control` | `1 failed in 3.18s` |
| `rechten-phone-viewer` | `1 failed in 3.22s` |
| `rechten-web-viewer` | `1 failed in 2.64s` |
| `import-web-v2` | `1 failed in 4.53s` |
| `import-web-v1` | `1 failed in 5.05s` |
| `import-cli-v2` | `1 failed in 5.17s` |
| `import-cli-v1` | `1 failed in 4.86s` |
| `levensloop-binden` | `1 failed in 3.14s` |
| `levensloop-ontkoppelen` | `1 failed in 2.25s` |
| `levensloop-vervangen` | `1 failed in 2.76s` |
| `levensloop-archiveren` | `1 failed in 2.88s` |
| `levensloop-getest` | `1 failed in 2.78s` |
| `adapter-api` | `1 failed in 10.03s` |
| `adapter-worker` | `1 failed in 3.21s` |
| `worker-actiematrix` | `18 failed in 4.65s` |
| `interne-kern` | `18 failed in 4.71s` |
| `websocket` | `1 failed in 2.40s` |
| `apparaat-discover` | `1 failed in 2.50s` |
| `apparaat-pair` | `1 failed in 3.80s` |
| `apparaat-probe` | `1 failed in 4.57s` |
| `apparaat-health` | `1 failed in 3.22s` |

## Laatste lokale poort

- Volledige API-suite: `631 passed, 3 warnings in 204.12s (0:03:24)`; geen skips.
- Daarna de herstelde adaptertesthulp met positieve controle: `2 passed, 78 deselected in 9.06s`.
- Integraties: `188 passed, 1 skipped in 13.66s`. De skip is `geen readback om te vervalsen`
  uit `integrations/tests/contract_kit.py`; die proef is niet als groen bewijs meegeteld.
- HomeWizard afzonderlijk: `9 passed in 0.06s`; HomeWizard-contract: `13 passed in 0.14s`.
- Platform-/bewijscontroles: `68 passed in 0.53s`. Ruff en online contract-/Peil-vergelijkingen in orde.
- OpenAPI opnieuw geëxporteerd; geen verschil met de bestaande momentopname.

De CI van de afsluitcommit en de drie exacte commitbereiken staan in het externe ketenrapport.
