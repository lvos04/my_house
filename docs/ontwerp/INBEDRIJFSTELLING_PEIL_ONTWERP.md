# Inbedrijfstelling in Peil — S-P3-710

**Status:** ontwerp vóór bouw · 18 november 2026 · A-ONTWERP, N3. Alleen dit document is gewijzigd.
**Basis:** `3d504973dcb6b08acf55565f1e5a961dc875d45b`. Dit ontwerp beschrijft gewenst gedrag; schermen,
migratie en nieuwe API-antwoorden zijn hier niet gebouwd of beproefd.

## 1. Bronnen en vaste keuzes

`graphify query 'Peil ontwerptaal bron componenten ontwerp'` wees naar `design/ontwerptaal/ONTWERPTAAL.md` en
`COMPONENTEN.md`; de afgekorte graaf is gevolgd door bronlezing. De lokale kopie pint Peil **1.1.1**; herkomst:
`design/ontwerptaal/HERKOMST.md`. Bestaande ontwerpen staan in `docs/ontwerp/PEIL_MIGRATIE.md` en `PEIL_REVIEW.md`;
de naam volgt het patroon van `ai-server-manager/docs/ontwerp/HUB_PEIL_ONTWERP.md`.

Verder gelezen: ADR-0005, `docs/platform-v2/DELTA_MATRIX.md` §9 en §11.6, het dossier en de verkenning van
S-P3-710, beide opgegeven reviews van S-P3-705, `Devices.tsx`, `DeviceCard.tsx`, `DeviceTools.tsx`, `Install.tsx`,
API-modellen, testmarkering, audit, herstel en de Android-foutafhandeling (bronnen hieronder).

De regisseursbesluiten uit `OPDRACHT-A-ONTWERP.md` gaan voor de afwijkende onderdelen van ADR-0005: bij een
bestaande installatie begint de gate uit; de nieuwe weigering krijgt een eigen code. **Niets markeert automatisch
als getest**, ook een succesvolle leestest niet. Een beheerder bevestigt bewust. Een beschermd apparaat blijft
altijd alleen meten, ongeacht teststatus of gate. Rechten, veilige modus en bestaande soortcontroles blijven gelden.

## 2. Installatietoestand, migratie en herstel

Bewaar één installatiebrede instelling `commissioning_gate` in de bestaande tabel `settings`, met de bestaande
`{"v": ...}`-omhulling (`store.py::get_setting/set_setting`). Voorgestelde waarde: `version: 1`, `enabled: boolean`,
`origin: fresh|existing_devices`, `enabled_at` en `enabled_by` (null zolang niet ingeschakeld). Geen browserinstelling,
omgevingsschakelaar of tweede beleidslaag. Alle woningen delen de gate; de taak noemt die reikwijdte.
Bij een nieuwe installatie registreert de initialisatie haar eigen tijd en systeemactor als inschakelgegevens.

| Moment | Betrouwbare vaststelling en resultaat |
|---|---|
| Eerste migratie, instelling ontbreekt | Controleer binnen dezelfde migratietransactie of **enige** rij in `devices` bestaat, inclusief gearchiveerde/vervangen apparaten en oude woningen. Ja: `origin=existing_devices`, gate uit, beheerderstaak. Nee: `origin=fresh`, gate aan. Blokkeer gelijktijdige ontdekking/import/workerstart totdat migratie en initialisatie zijn vastgelegd. |
| Instelling bestaat | Behoud haar; opnieuw starten of migreren leidt niet tot een nieuwe classificatie. Een lege lijst na archiveren/verhuizen schakelt niets om. |
| Na invoering ontbrekend, onleesbaar of onbekend formaat | Behandel de gate als aan en toon onbekende configuratie met herstelhulp. Een leesfout geeft nooit een vrijstelling. De eenmalige migratie-uitzondering is geen runtime-default. |
| Inschakelen | Alleen beheerder, expliciete bevestiging, atomair instelling + audit `commissioning_gate_enabled`. Alleen overgang uit → aan; herhaald verzoek heeft geen tweede effect. Geen uitschakelknop of algemene settings-write die dit terugdraait. |

Dit herkent de afgesproken categorie **“er staan apparaten vóór de update”** betrouwbaar uit lokale data.
Het bewijst niet dat die apparaten echt werken: `online`, leeftijd, een woningrij of `confirmed` maken niemand
automatisch getest. Een eerder leeggeruimde database of losse import zonder herkomst laat historisch gebruik niet
betrouwbaar herkennen. Mogelijke wegen: de oude database/export vóór de update onderzoeken, een gecontroleerd
herstel uitvoeren, of als nieuwe installatie behandelen en handmatig testen/bevestigen. Geen tijdstempelheuristiek.

**Herstel:** `backup.py::_restore_sync` leegt ook `settings`, zet zo nodig het schema terug en migreert daarna omhoog;
`_after_restore` heeft daarnaast een afzonderlijke veilige modus. A-BOUW moet vóór het legen de bestaande gatetoestand
vasthouden en die na laden/migreren **in dezelfde transactie** samenvoegen. Een eerder ingeschakelde gate blijft aan,
ook bij een back-up zonder instelling of met uit. Een geldige aan in de back-up blijft eveneens aan. Alleen een
geldige uit op een bestaande installatie mag uit blijven; bij ontbreken van zo'n bestaande toestand en een oude
back-up bepaalt de eenmalige migratie de toestand op basis van de geladen apparaten. Een reeds als nieuw
geïnitialiseerde installatie heeft aan en blijft dus aan, ook bij import van oude gegevens. Onleesbare toestand:
aan. Bij een mislukte hersteltransactie blijft de toestand van vóór herstel gelden. Nieuwe woning: gate blijft
installatiebreed behouden. Audit de herstelbeslissing zonder geheimen; behoud de inschakelgegevens van vóór herstel
als die strenger waren dan de back-up. Vrijgave van veilige modus schakelt de gate niet uit en markeert niets getest.

**Import:** geen algemene importvrijstelling. Een import in een draaiende installatie behoudt de gate; ingevoerde
apparaten zonder geldig bestaand testbewijs blijven `untested`. Een volledige, gecontroleerde back-up bewaart bestaande
testmarkeringen en audit; losse import of vervanging kopieert niet stil een testmarkering naar een ander apparaat.
Ook wachtrijen, oude knopkoppelingen, retries en bediening na herstart/herstel worden bij uitvoering opnieuw getoetst.
Een volledige terugzetting van alle opslag buiten de applicatie kan de applicatie niet herkennen; voor behoud van
een eerdere inschakeling is dan de externe herstelprocedure/registratie nodig. Leg die beperking vast bij herstel.

## 3. Apparaten: één beheerderstaak en historie

Behoud `/apparaten` en de bestaande apparaatkaarten. Boven de integraties komt bij gate uit een taakpaneel
**“Bevestig de bestaande apparaten”**: toestand en gevolg, aantallen ongetest/niet ondersteund, link naar de
bevestigingslijst en knop “Beveiliging inschakelen”. De taak blijft zichtbaar tot de server aan teruggeeft;
wegklikken of wizardvoortgang verbergt haar niet blijvend. Bij aan staat een rustige neutrale regel met tijd.

De lijst gebruikt bestaande apparaat-ID's en toont naam/ruimte, teststatus, bescherming en **laatste bevestigde
bediening** naast elkaar. Bron: nieuwste `commands.status=confirmed` voor exact `home_id` en `device_id`, met
`updated_at` als bevestigingstijd (`worker/commands.py::_record_result`). Toon tijd en vertaald actiewoord;
`queued/sending/sent/failed/denied` zijn geen bevestiging. Geen record betekent “Geen bevestigde bediening bekend”,
ook bij een alleen-lezen meter; dit is geen mislukte test. Geen overerving van het vervangen apparaat of een
groepbevestiging naar alle lampen. Deze historische aanwijzing is geen actuele gezondheid of bewijs van menselijke
validatie. Toon de gegevens met bron en ophaaltijd; oude historie is op zichzelf niet verouderde ophaaldata.

Per apparaat: “Leestest uitvoeren”, hulp “Leest alleen uit; schakelt niets”, daarna apart “Getest bevestigen”.
De bevestiging toont naam en gevolg, een optionele notitie (bestaande limiet 500), Annuleren en “Als getest markeren”.
Meerdere tegelijk: expliciete individuele selectie, geen vooraf aangevinkte apparaten of selectie van verborgen
pagina's; samenvatting met alle gekozen namen, een niet-aangevinkte bevestigingscheckbox en knop
“{count} apparaten als getest markeren”. Geen selectie blijft focusbaar uitgelegd. Server hercontroleert alle ID's,
rechten en geschiktheid in één transactie: alles vastleggen of niets. Bij drift selectie bewaren maar opnieuw laten
beoordelen. Per veranderd apparaat één `device_marked_tested`, ook bij bulk; geen herhaald effect door dubbelklik.
Bulkselectie bevat alleen actieve, niet-beschermde apparaten; gearchiveerde/vervangen doelen blijven in hun bestaande
overzicht. Beschermde apparaten kunnen per apparaat expliciet voor **meten** getest worden, met blijvend BeleidsSlot.
`unsupported` eerst oplossen en opnieuw uitlezen; geen handmatige overschrijving van protocolfalen.

Inschakelen is een afzonderlijke bevestiging met de installatiebrede reikwijdte en actuele aantallen. Ongeteste
apparaten mogen overblijven: de dialoog noemt het aantal dat hierdoor niet bediend kan worden; nooit eerst alles
automatisch markeren. Bevestiging is niet vooraf aangevinkt. Na vastlegging opnieuw ophalen, taak vervangen door
de toestand aan en statusbericht aankondigen. Bij onbekende mutatie-uitkomst eerst ophalen; niet blind opnieuw muteren.

Onder **Beheren → Testen**: uitklapbaar “Testgeschiedenis van {name}”, nieuwste boven, cursor “Meer laden”. Gebruik
AuditTijdlijn met tijd, actor, “Als getest gemarkeerd”, model en firmware uit die auditregel. Historische ontbrekende
waarden heten “Niet vastgelegd”. De huidige `diagnostics.tested` is alleen de laatste bevestiging; de huidige notitie
mag niet op oude regels worden geplakt. Bestaande audit bevat `detail.device`, model en firmware, maar geen historische
notitie. Als notities voortaan in audit komen, alleen expliciete geschoonde gegevens en nooit meer claimen voor oude
regels. Filter **server-side** exact op apparaat-ID en toegestane woning, vóór sortering/limiet/paginering.
`GET /audit` filtert nu alleen actie/actor; eerst globaal ophalen en in de browser filteren is onvoldoende.

## 4. Weigering, alle bedieningswegen en Android

Bij gate aan en `compatibility != tested`: HTTP **409**, bestaand `detail`-foutformaat, nieuwe code
**`device_not_tested`**, Nederlandse `message` uit `commissioning.denied` in §7. Geen 202 of command-ID bij een
directe weigering. Bescherming heeft voorrang: ook een geteste boiler geeft `policy_denied`. Daarna geeft elke
niet-geteste status, inclusief `unsupported` of ontbrekend, de eigen gatecode. Bestaand protocol-/soortbeleid blijft
daarnaast gelden; een testlabel maakt een niet-ondersteund commando niet uitvoerbaar. Rechten/woningafscherming
gaan vóór het vrijgeven van apparaatgegevens.

Web toont GateBlokkade bij de bediening, naam van het doel, reden en vervolglink. Bij een fout van dit verzoek
wordt de uitleg eenmaal aangekondigd; een vooraf zichtbare blokkade heeft geen alarmerende live-regio. Beheerder:
“Naar Apparaten”; bediener: vraag een beheerder, geen beheeractie. Bekende blokkade: knop `aria-disabled` met
zichtbare reden; verander geen weergegeven apparaatstand en zet niets offline in een wachtrij. Regelaar:
“OEM regelt zelf”, met de ongetest-reden. Bij gate uit bestaat deze nieuwe blokkade nog niet, de andere blokkades wel.

De bestaande `policy.py::check_command`, commandokern en worker-precheck moeten dit delen. Dossierdekking:
zonecommando's (web/Android), lichtgroepen én scènes inclusief hun groep, knopopslag/simulatie/fysieke events/replay,
schema/regelgrenzen/hervatten/profiel en hun uitvoering, experimenten/planner/adaptief, wachtrij/retry/herstart,
herstel/import/vrijgave, vervangen/binden/reactiveren/soortopwaardering met daaropvolgende bediening. Configuratie
is geen bewijs van testen. Oude boilerzone: schema, beleid, hervatten en profiel uniform `409 policy_denied`
**vóór mutatie**, inclusief atomair behoud bij meerdere zones. Bij workerweigering: bestaande opdracht naar denied,
reden zichtbaar in opdrachtstatus, nul adapteraanroepen. Websocket heeft geen bedienroute. Ontdekking, pairing,
diagnose en leestest blijven lezen/koppelen. `permit_join` alleen als expliciet beheercommando aan een bridge
afzonderlijk afbakenen/testen; geen generieke uitzondering voor “beheerder”. Toekomstige connector/Hub-routes
sluiten dezelfde kern aan (S-P3-740/742 en S-P3-130); die routes bestaan hier nog niet.

**Android nu, uit bron vastgesteld:** `core/.../ApiClient.kt::parseError` bewaart `detail.code` en een niet-lege
`detail.message` als `ApiException(fromApi=true)`. Een API-409 is `APP_ERROR` (`Network.kt::Failures.classify`),
geen ontkoppeling of onbereikbare route; `CoreTexts.message` geeft de servertekst door. `HomeRepository` meldt de
verzendfout via het berichtenkanaal, verwijdert een klimaatconcept en registreert geen geaccepteerde opdracht.
Zonder bruikbare boodschap gebruikt 409 `ApiProblem.CONFLICT`: “Dit kan nu niet.” De nieuwe code past dus in het
bestaande formaat, maar een Engelse app krijgt nu óók Nederlandse servertekst. A-BOUW voegt voor deze code een
lokale NL/EN-resource toe (sleutel §7, Android-naam `commissioning_denied`); onbekende codes houden de servertekst.
Geen automatische herhaling, routewissel of nieuwe koppeling wegens deze 409. Dit is bronlezing, geen Android-run.

## 5. Wizard en rechten

Behoud `/installatie`, de bestaande controles/woning/adressen en slotlinks. Daarna: **meters (HomeWizard P1 en
rollen, uitsluitend lezen) → Hue lezen → Hue testen → Gree na D12 → overige Zigbee/sensortoewijzingen → overzicht**.
De huidige wizard zet Hue vóór HomeWizard en heeft geen aparte lees-/teststap. Hue-test: bestaande leestest,
menselijke controle/readback en expliciete bevestiging. Bij gate aan mag de wizard vóór die bevestiging geen
gewone schakelopdracht versturen; fysieke controle gebeurt met de bestaande fabrikantbediening. Daarna kan één
bewuste bediening met readback via de bestaande commandokern worden gecontroleerd. Geen verborgen testomweg.

Gree toont GateBlokkade met `commissioning.d12Wait`. Geen bind-/schrijfproef zolang de echte modelspecifieke
bind/readbackcontrole ontbreekt. De bron/registratie van D12 is nog vast te stellen door A-BOUW; ontbreken is
onbekend en houdt Gree dicht. Geen D12-vinkje in localStorage, geen vrijgave door bladeren of “Volgende”. Bied
“Gree later instellen” zodat de basis kan worden ingericht; Gree blijft als wachtend zichtbaar, nooit als getest.
Na geldig D12: bestaande protocolgate, leestest en expliciete menselijke bevestiging; daarna één bevestigde
setpointwijziging. Migreer oude wizardvoortgang op semantische sleutels: een oude stap `hue` bewijst geen nieuwe
Hue-test; oude `gree` bewijst D12 niet. Na terugkeer haalt de wizard servertoestand op.

| Rol | Ziet / mag |
|---|---|
| Beheerder (`Principal.is_admin`, inclusief admin-telefoon bij ondersteunde routes) | Taak, lijst, bevestigde bediening, historie; leestest, expliciet markeren, bulk en gate inschakelen. Native Android krijgt hiermee geen nieuw beheerscherm; beheer loopt via bestaande web-app. Server dwingt rechten af. |
| Telefoon `control` / bediener | Toegestane apparaatstatus en eigen bedienweigering; geen historie met actoren, taak, selectie of markeer-/inschakelrechten. Gate geldt ook voor beheerder bij gewone bediening. |
| Kijker (`viewer`) | Alleen toegestane apparaatstatus en leesuitleg; geen bediening of beheermutaties; één RolMelding. Rechtstreekse mutatie geeft 403. |
| Kindprofiel | Peil-kindschil Home/Kijken/Zoeken/Ik, geen Huis/Apparaten/wizard/audit of beheertellers. Rechtstreekse toegang geweigerd met kindtekst. my_house kent nu admin/viewer en telefoonrollen, geen zelfstandig kindbeheer: voeg hier geen rol of accountmigratie toe; een aangeleverd kindprofiel mag nooit tot control/admin worden opgewaardeerd. |

## 6. Peil, toestanden en toegankelijkheid

Alleen bestaande onderdelen: PageHeader C-02, neutrale feitstijl C-03a, Versheid C-06, GateBlokkade C-11,
BeleidsSlot C-13, Foutpaneel C-14, Leeg/Onbekend/Laden C-17/18, BevestigFlow C-24 met AlertDialog,
AuditTijdlijn C-25/27, ResponsieveLijst C-34, Wizard C-36, RolMelding C-49 en TestResultaat C-58.
Basis: bestaande Button/Card/Checkbox/Label; React 18-varianten volgens `PEIL_MIGRATIE.md`. Het ontbreken van
een lokale AlertDialog-wrapper vraagt hergebruik van het bestaande Peil-recept, geen nieuwe ontwerptaal.

Tokens: `background/card/popover`, `foreground/muted-foreground`, `border/border-strong/input`,
`primary/primary-foreground/link`, `gate/gate-bg/gate-ink`, `unknown*`, `stale*`, `down*`, `ring/ring-halo`,
`touch`, `content-max`, `prose-max`, `z-overlay`; Tailwind-schaal `p-4`, `gap-4`, secties `gap-6`, `rounded-lg`.
Teststatus is neutraal met tekst en kolf-icoon; geen groene gezondheidsclaim. Bescherming gebruikt verbodsteken,
wachten een slot. Geen nieuwe token, kleur, maat of componentdefinitie. **Peil-wijziging nodig: nee.**

| Breedte, in licht én donker | Opbouw |
|---|---|
| 320 | Eén kolom, bestaande ondernavigatie. Taak boven inhoud; kaartvelden als `dl`, acties gestapeld op volle breedte. Selectielabels lopen om; geen sticky bulkbalk. Dialoog binnen viewport, inhoud verticaal bereikbaar; geen afgekapt naam-/gevolgveld. |
| 390 | Zelfde volgorde, extra ruimte voor naam en teststatus; uitlegregel blijft zichtbaar. Wizardacties passen boven ondernavigatie met scroll-padding. |
| 768 | Bevestigingslijst als tabel met hoogstens vier kolommen: selectie/naam, status/bescherming, bevestigde bediening, acties; overige details uitgeklapt. Historie onder het eigen apparaat, geen horizontale scroll. |
| 1440 | Bestaande desktopnavigatie, inhoud begrensd door `content-max`; dezelfde taak en vierkolomslijst. Leesuitleg maximaal `prose-max`; geen verspreiding van bevestiging en gevolg over verre kolommen. |

Leeg: geen apparaten → uitleg en beheerderlink naar toevoegen, inschakelen blijft een bewuste keuze als gate uit.
Geen historie en geen bevestigde bediening hebben eigen lege tekst. Bezig: eerste laden C-18 met `aria-busy`, na
10 s langere-wachttijdtekst; verversen behoudt vorige inhoud. Muteren: label met ellips, geen dubbele verzending.
Fout: sectie-Foutpaneel, selectie behouden, “Opnieuw proberen” voor ophalen. Onbekend/offline: geen lege-lijstclaim;
laatst bekende gegevens met bron/tijd, verouderd gearceerd, mutaties uit met reden. Geen rechten: §5, geen verborgen
mutatie-API achter een zichtbare knop. Een mislukte inschakeling wordt nooit als aan getoond.

Eén h1, logische koppen, `fieldset/legend` voor selectie; label “Selecteer {name}”, actienaam met apparaatnaam,
`aria-describedby` voor gevolg/reden. Tab: taaklink → inschakelen → selectie/rijacties → historie → meer laden.
Enter/spatie activeert echte knoppen/checkboxes; geen klikbare kaart rond andere knoppen. AlertDialog benoemt titel
en gevolg, beginfocus Annuleren, focusval binnen dialoog, Esc sluit, terugfocus naar opener; na bevestigen melding
en geactualiseerde toestand, geen onverwachte paginawissel. Wizardstapwissel focust h2, `aria-current=step`.
Alle doelen ≥44×44 px, tekst ≥14 px (invoer ≥16), tekstcontrast ≥7:1, betekenisdragende randen en **zichtbare,
niet-geanimeerde focus ≥3:1**. Peil-ring 3 px `ring` + 2 px `ring-halo`; nooit bedekt door balken. Bij forced colors:
`CanvasText`-randen met behoud van stippelstijl, `Highlight`-focus en onderstreepte huidige stap/selectie; geen
`forced-color-adjust:none`. Iconen naast tekst `aria-hidden`. Tijd via `<time>` in woningtijdzone, absolute én
relatieve tijd. Alleen nieuw verzoek: fout `role=alert`; resultaat één beleefde aankondiging, geen hele live-tijdlijn.

## 7. Exacte teksten en vertaalsleutels

Onderstaande sleutels zijn het nieuwe copycontract, geen claim dat ze al bestaan in `apps/web/src/i18n.ts`.
Android spelt gebruikte sleutels met underscores. `{name}`, `{count}`, `{time}`, `{action}` en `{actor}` zijn veilige
tekstinterpolaties; geen HTML, adressen, geheimen of rauwe diagnostiek. Aantallen gebruiken locale pluralisatie.
Ongewijzigde omliggende schermteksten blijven buiten deze toevoeging.

| Sleutel `commissioning.` + | Nederlands | English |
|---|---|---|
| taskTitle | Bevestig de bestaande apparaten | Confirm existing devices |
| taskBody | De beveiliging voor ongeteste apparaten staat nog uit. Bevestig zelf welke apparaten werken, of voer eerst de leestest uit. Schakel daarna de beveiliging in voor alle woningen. | Protection for untested devices is still off. Confirm which devices work, or run the read-only test first. Then enable protection for all homes. |
| review | Apparaten beoordelen | Review devices |
| listTitle | Teststatus van apparaten | Device test status |
| name | Apparaat en ruimte | Device and room |
| status | Teststatus | Test status |
| actions | Acties | Actions |
| untested | Nog niet getest | Not tested yet |
| tested | Getest in dit huis | Tested in this home |
| unsupported | Niet ondersteund | Not supported |
| counts | Nog niet getest: {count}. Niet ondersteund: {unsupportedCount}. | Not tested yet: {count}. Not supported: {unsupportedCount}. |
| lastConfirmed | Laatste bevestigde bediening | Last confirmed operation |
| confirmedDetail | {action} · {time} | {action} · {time} |
| noneConfirmed | Geen bevestigde bediening bekend | No confirmed operation recorded |
| historyHint | Een eerdere bevestiging is een hulpmiddel. Controleer zelf of het apparaat werkt. | A previous confirmation is a guide. Check that the device works yourself. |
| source | Bron: opdrachthistorie · opgehaald {time} | Source: command history · retrieved {time} |
| select | Selecteer {name} | Select {name} |
| selection | Geselecteerde apparaten: {count} | Selected devices: {count} |
| noSelection | Selecteer eerst de apparaten die je zelf hebt gecontroleerd. | First select the devices you have checked yourself. |
| readTest | Leestest uitvoeren | Run read-only test |
| readOnly | Leest alleen uit; schakelt niets. | Reads only; does not switch anything. |
| readBusy | Leestest uitvoeren… | Running read-only test… |
| readOk | Leestest uitgevoerd; bevestig zelf of het apparaat werkt. | Read-only test completed; confirm whether the device works. |
| mark | Getest bevestigen | Confirm tested |
| markNamed | Getest bevestigen: {name} | Confirm tested: {name} |
| markTitle | {name} als getest markeren? | Mark {name} as tested? |
| markBody | Bevestig alleen als je meten en, waar toegestaan, bedienen zelf hebt gecontroleerd. De teststatus wordt vastgelegd; dit schakelt het apparaat niet. | Confirm only if you have checked readings and, where allowed, operation yourself. The test status will be recorded; this does not switch the device. |
| note | Opmerking bij de test (optioneel) | Test note (optional) |
| markSubmit | Als getest markeren | Mark as tested |
| bulkTitle | Geselecteerde apparaten als getest markeren? | Mark selected devices as tested? |
| bulkCheck | Ik heb alle geselecteerde apparaten zelf gecontroleerd. | I have checked all selected devices myself. |
| bulkSubmit | {count} apparaten als getest markeren | Mark {count} devices as tested |
| bulkReason | Bevestig eerst dat je alle geselecteerde apparaten hebt gecontroleerd. | First confirm that you have checked all selected devices. |
| markBusy | Teststatus vastleggen… | Recording test status… |
| marked | Teststatus vastgelegd voor {count} apparaten. | Test status recorded for {count} devices. |
| enable | Beveiliging inschakelen | Enable protection |
| enableTitle | Beveiliging voor ongeteste apparaten inschakelen? | Enable protection for untested devices? |
| enableBody | Dit geldt voor alle woningen. Daarna worden ongeteste apparaten niet meer bediend, ook niet door schema’s, knoppen en de regelaar. Dit geldt ook voor wachtende opdrachten. De beveiliging blijft aan. Beschermde apparaten blijven altijd alleen meten. | This applies to all homes. Untested devices will no longer be operated, including by schedules, buttons and the controller. This also applies to queued commands. Protection stays on. Protected devices always remain read-only. |
| remaining | Nog niet als getest vastgelegd: {count} apparaten. Die kunnen daarna niet bediend worden. | Not recorded as tested yet: {count} devices. They cannot be operated afterwards. |
| enableCheck | Ik begrijp dat ongeteste apparaten daarna niet meer bediend worden. | I understand that untested devices can no longer be operated afterwards. |
| enableReason | Bevestig eerst dat je het gevolg begrijpt. | First confirm that you understand the effect. |
| enableBusy | Beveiliging inschakelen… | Enabling protection… |
| enabled | Beveiliging voor ongeteste apparaten staat aan sinds {time}. | Protection for untested devices has been on since {time}. |
| cancel | Annuleren | Cancel |
| denied | Dit apparaat is nog niet getest. Laat een beheerder het eerst onder Apparaten testen en als getest bevestigen in de web-app. Er is geen bedienopdracht verstuurd. | This device has not been tested yet. Ask an administrator to test it and confirm it as tested under Devices in the web app first. No control command was sent. |
| devices | Naar Apparaten | Go to Devices |
| askAdmin | Vraag een beheerder om dit apparaat te testen en als getest te bevestigen. | Ask an administrator to test this device and confirm it as tested. |
| oem | OEM regelt zelf · dit apparaat is nog niet getest. | OEM controls the device · this device has not been tested yet. |
| protected | Alleen meten — schakelen blijft altijd geblokkeerd, ook na een testbevestiging. | Read-only — switching always remains blocked, even after confirmation as tested. |
| unsupportedHelp | Los eerst de protocolfout op en lees het apparaat opnieuw uit. | Resolve the protocol error first, then read the device again. |
| history | Testgeschiedenis van {name} | Test history for {name} |
| historyEntry | {time} · {actor} · als getest gemarkeerd | {time} · {actor} · marked as tested |
| model | Model | Model |
| firmware | Firmware | Firmware |
| notRecorded | Niet vastgelegd | Not recorded |
| noHistory | Nog geen testbevestigingen vastgelegd voor dit apparaat. | No test confirmations recorded for this device yet. |
| more | Meer laden | Load more |
| empty | Nog geen apparaten gekoppeld. Voeg eerst een apparaat toe. | No devices linked yet. Add a device first. |
| add | Apparaat toevoegen | Add device |
| loading | Teststatus ophalen… | Loading test status… |
| longWait | Teststatus ophalen… dit duurt langer dan verwacht. | Loading test status… this is taking longer than expected. |
| refreshing | Bijwerken… | Updating… |
| fetchError | Teststatus ophalen is mislukt. Probeer het opnieuw. | Loading test status failed. Try again. |
| mutationError | De wijziging kon niet worden bevestigd. Haal de toestand opnieuw op voordat je verdergaat. | The change could not be confirmed. Reload the state before continuing. |
| retry | Opnieuw proberen | Try again |
| unknown | Onbekend — de teststatus kon niet worden opgehaald. | Unknown — the test status could not be retrieved. |
| stale | Verouderd · laatst bekende stand van {time}. | Stale · last known state from {time}. |
| offline | Wijzigen kan nu niet: je bent offline. Haal na verbinding eerst de actuele toestand op. | Changes are unavailable while offline. Retrieve the current state after reconnecting. |
| configUnknown | De beveiligingsinstelling is onbekend. Ongeteste bediening blijft geblokkeerd. Laat een beheerder de instelling controleren. | The protection setting is unknown. Untested operation remains blocked. Ask an administrator to check the setting. |
| viewer | Je kijkt als kijker: je kunt hier niets wijzigen. | You are viewing as a viewer: you cannot change anything here. |
| forbidden | Alleen een beheerder mag dit doen. | Only an administrator may do this. |
| child | Dit is voor een volwassene. | This is for an adult. |
| childDetails | Details voor een volwassene | Details for an adult |
| meters | Meters uitlezen | Read meters |
| hueRead | Hue uitlezen | Read Hue |
| hueTest | Hue testen en bevestigen | Test and confirm Hue |
| gree | Gree controleren | Check Gree |
| d12Wait | Gree-bediening wacht nog op een echte controle van koppelen en teruglezen voor jouw aircomodel en firmware. Tot die controle is vastgelegd, blijft bedienen via deze app uit. Gebruik de eigen afstandsbediening. Je kunt de rest van de installatie wel afmaken. | Gree operation is waiting for a real pairing and readback check for your air conditioner model and firmware. Operation through this app remains disabled until that check is recorded. Use its own remote control. You can complete the rest of the installation. |
| greeLater | Gree later instellen | Set up Gree later |
| waiting | Wacht op controle | Waiting for a check |
| wizardStep | Stap {step} van {total}: {title} | Step {step} of {total}: {title} |
| wizardNav | Stappen van de installatie | Installation steps |
| previous | Vorige | Previous |
| next | Volgende: {title} | Next: {title} |
| baseConfigured | De basis is ingericht. Wachtende apparaatcontroles blijven zichtbaar bij Apparaten. | Basic setup is configured. Pending device checks remain visible under Devices. |
| replacementBlocked | Vervangen kan niet: een knopkoppeling verwijst nog naar dit beschermde apparaat. Verwijder eerst die koppeling. | Replacement is blocked: a button mapping still refers to this protected device. Remove that mapping first. |

## 8. Genummerde meetpunten voor R-ONTWERP en e2e

Alle resultaten hieronder zijn **nog te meten na bouw**, met fictieve gegevens en zonder echte hardware/accounts.

1. Migratiefixtures: leeg → aan; bestaande actieve én uitsluitend gearchiveerde apparaten → uit; bestaande `tested_at`, status en audit ongewijzigd; op/neer/op verliest geen eerder ingeschakelde gate.
2. Herstel/importmatrix: oud/nieuw schema, gate ontbreekt/uit/aan, doel uit/aan, nieuwe woning, fout/rollback en veilige-modusvrijgave. Eerder aan wordt nooit uit; geen automatische testmarkering of oude wachtrij-omweg.
3. Bestaande installatie: taak blijft na herladen zichtbaar; laatste bediening uitsluitend `confirmed`, per exact apparaat/woning; geen record heeft de lege tekst. Geen overerving bij vervangen.
4. Per-apparaat- en bulkbevestiging: bewuste niet-vooringevulde keuze, alle namen, annuleren zonder mutatie, atomair foutgedrag/drift/dubbelklik, per apparaat audit. Ontdekking/pairing/leestest verandert teststatus niet en schakelt niets.
5. Inschakelen: expliciet gevolg en actuele resterende aantallen; na bevestiging blijvend aan met audit, ook na herstart. Fout/onbekende uitkomst toont geen schijnbevestiging; ophalen herstelt werkelijke toestand.
6. Gateketen uit §4: ongetest, `unsupported` en ontbrekende status bij aan geven eigen weigering, ook beheerder en worker; nul adapteraanroepen. Positieve geteste tegenhanger werkt binnen alle overige beperkingen; boiler altijd `policy_denied`. Gate uit behoudt bestaande bediening.
7. Historie: twee woningen, meerdere apparaten, oude ontbrekende velden en meerdere pagina's; filtering vóór limiet, nieuwste boven, geen huidige notitie op oude regels. Niet-beheerders krijgen geen actorhistorie.
8. Wizard: meters → Hue lezen → Hue testen → Gree; wachten in gewone taal zonder D12-vrijgave via browservoortgang. Gree later laat basis doorgaan; oude voortgang markeert geen nieuwe teststap af.
9. Android: eigen JSON-409 bewaart code/uitleg, Nederlands en Engels gebruiken de specifieke resource; geen command-ID, retry/routewissel/ontkoppeling, klimaatconcept weg. Kale 409 houdt bestaande algemene fouttekst.
10. Beheerder/control/viewer/kind: zichtbaarheid volgens §5 en directe verboden API-verzoeken 403; geen rolopwaardering, woninglek of beheerteller in kindschil.
11. Afdrukken en DOM-metingen: 320/390/768/1440 × licht/donker voor uit/aan, selectie, beide bevestigingen, historie, leeg, laden, fout, onbekend/verouderd en D12-wachtstand; 0 px paginaoverloop én geen inhoud buiten eigen element. Aanvullend Peil 360/1024, DejaVu Sans en 200% zoom; doelen ≥44 px, tekst ≥14 px, tekstcontrast ≥7:1.
12. Volledige Tab/Shift-Tab-doorloop op 390 en 1440, dialogen ook 320: namen/labels, begin- en terugfocus, Esc, spatie/Enter, geen bedekte focus; elke ring ≥3:1 en niet geanimeerd. Forced colors bewaart randstijl/selectie/focus; reduced motion geen beweging; axe WCAG 2.2 AA 0 bevindingen. Echte schermlezer blijft afzonderlijke acceptatie.
13. S-P3-705-vervolg: gelijktijdig binden/vervangen/opslaan versus gate-inschakelen serialiseren; oude boilerzone weigert vóór alle wijzigingen; simulatieproef E8 en een mutatie uitsluitend op de groepscontrole in `_dry_run`; V3-tekst noemt de koppeling, V4 blokkeert niet onterecht vervanging van dezelfde soort.

## 9. Open werk voor A-BOUW

- Migratie: herbevestig het volgende vrije nummer onder `apps/api/src/shc_api/migrations/versions/` (nu eindigend op 0004). Voeg alleen de ontbrekende apparaatkolommen **detectiemethode** en **terugval** toe: detectie `scan|manual`, onbekend null; terugval nullable tekst, geen verzonnen standaardwaarde. Bestaande herkomst alleen vullen als brondata het bewijst. Geen nieuwe host-, protocolversie-, firmware- of testlogvelden/tabel. Bestaande gegevens gemaskeerd samenbrengen in de compatibiliteitsmatrix; test op/neer/op en gegevensbehoud onafhankelijk.
- Bouw het gatecontract, de monotone instelling, strikte invoervalidatie, audit, gelijktijdigheidsbeveiliging en de herstelmerge. Laat downgrade de instelling behouden; anders kan op/neer/op na inschakelen onterecht uit opleveren. Uitvoeringscontrole mag geen verouderde cache gebruiken. Geen productieherstel draaien om dit te bewijzen.
- Breid bestaande API-weergaven gericht uit met gate/laatste bevestigde bediening en veilige per-apparaat-auditfiltering, plus beheerdermutaties voor bulk en inschakelen. Leg endpoint-/payloadkeuze en nieuwe code vast in foutcatalogus/OpenAPI, TypeScript en tests; voeg geen algemene settings-achterdeur toe. Historische notitie ontbreekt nu: toon alleen wat werkelijk opgeslagen is.
- D12-bron en autorisatie van de vastlegging zijn nog ONBEKEND; vaststellen vanuit het bestaande installatie-/bewijscontract, zonder echte airco te benaderen. Tot dan Gree wachtend; geen nieuwe vrije D12-override. Wijzig ADR-0005/release-uitleg bij bouw zodat eigen foutcode en bestaande-installatieovergang overeenkomen met dit besluit.
- S-P3-705: **V2** gezamenlijke vergrendeling/serialisatie voor binden, vervangen en koppeling opslaan; **V3** vervangweigering met koppeling als reden; **V4** oude onuitvoerbare knopstap versus vervangen door dezelfde soort; simulatiegat **E8/M2** afzonderlijk bewijzen. Oude zone met boilerunit: schema/beleid/hervatten/profiel atomair weigeren vóór mutatie. Geen dubbele soortbeleidslaag.
- Voer meetpunten 1–13, verplichte API-/OpenAPI-/webcontroles en de onafhankelijke reviews uit. R-HARDE-EIS blijft nodig voor migratie/herstel. Fysieke Hue/HomeWizard/Gree-acceptatie, echte NAS en accounts blijven NIET GEVALIDEERD; dit ontwerp verandert geen EVIDENCE-status. Energy Socket-uit hoort bij S-P3-715; toekomstige connector/Hub-aansluiting bij S-P3-740/742 en S-P3-130.
