# ADR-0012 — Contract 1.3 en Peil 1.1.1 in my_house: kopieën met pin, HUB_URL volgens §3.2

- **Status:** aangenomen (2026-11-10, conformiteitsronde contract 1.3)
- **Context-ID's:** C1.3-MH-01, C1.3-MH-02, C1.3-MH-07 (NU), C1.3-MH-03…06 (P3, open), C1.3-ALG-01/02/05;
  `docs/platform-v2/PLATFORM_CONTRACTEN.md` 1.3 (kop, §2.3 regels 2, 5 en 7, §3.2, §10, §11, §13),
  `docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md` §8, `design/ontwerptaal/ONTWERPTAAL.md` 1.1.1 §4.1, §6, §14, §16;
  ADR-0002, ADR-0008, ADR-0009, ADR-0010.

## Context

my_house had nog een kopie van contract **1.1** zonder drifttest, een EVIDENCE.json met vrije tekst in `validatie`, en
een gepinde Peil-kopie **1.1.0** uit de map `definitief/` (buiten de repos) zonder bron-commit. Contract 1.3 vraagt nu:

- een byte-gelijke kopie van het contract en van de conformiteitslijst met een drifttest tegen een vaste bron-commit
  (kop, §10), en een EVIDENCE-test volgens §11 (1.3);
- `HUB_URL` (alleen voor "Terug naar de Hub") gecontroleerd volgens de adresregels van §3.2; ongeldig of leeg ⇒ geen
  link; nooit uit `Referer` of de URL; geen enrollment; geen manifestparameter (§2.3 regel 5, RV5);
- de delta-matrix zonder enrollment (C1.3-MH-07);
- Peil-verbeterpunten alleen via de nieuwe Peil-versie, met de pin- en driftregel E08 (versie én commit-SHA; falen bij
  afwijking of dezelfde versie met andere inhoud; een nieuwere versie is een waarschuwing).

## Besluit

1. **Contractkopie met pin.** `tools/contract_kopie.py` (standaardbibliotheek) schrijft en bewaakt de kopie;
   `docs/platform-v2/CONTRACT_HERKOMST.json` pint versie `1.3`, bron-commit `02b119c…` en de sha256 van beide bestanden.
   CI faalt als een kopie afwijkt van de pin of van de bron op de gepinde commit (`vergelijk --online`, de bronrepo is
   publiek). Een **latere** wijziging in de bron is alleen een waarschuwing: het contract laat de kopie de vaste
   bron-commit volgen en noemt overnemen een stap van de orkestratie. Zo wordt de CI van my_house niet rood van een
   wijziging in een andere repo, maar blijft drift tegen de pin altijd een fout.
2. **Peil-kopie met pin (E08).** `tools/peil_kopie.py` bewaakt `design/ontwerptaal/` (7 bestanden), de twee kopieën in
   de web-app en de kop van `PeilTokens.kt`; `design/ontwerptaal/HERKOMST.json` pint Peil `1.1.1` op bron-commit
   `4ff4b3f…` van de werkbranch. Hier geldt E08 letterlijk: dezelfde versie met andere inhoud in de bron is een fout,
   een nieuwere versie een waarschuwing. De versie van een bestand zonder eigen versieregel (`components.json`) is die
   van `tokens.json` op dezelfde stand.
3. **Tests zonder database.** De platformtests staan in `apps/api/tests/platform_v2/` (zoals de conformiteitslijst
   vraagt) met een eigen `conftest.py` die de database-fixture van de API-tests uitschakelt; ze draaien in de volledige
   suite mee en los met `pytest tests/platform_v2`.
4. **HUB_URL: één set gevallen, twee controles.** Het startscript van de webcontainer schrijft `/_shc/config.json`; de
   web-app controleert de waarde nog een keer (`src/hub.ts`). Beide volgen dezelfde 103 gevallen in
   `infra/tests/hub_url_gevallen.json`. De controle in het script draait in POSIX-awk via `ENVIRON` (geen
   backslash-interpretatie, geen intervallen, zodat busybox-awk in de Alpine-image, mawk en gawk hetzelfde doen).
   **Strenger dan §3.2, met reden:** IPv6 met IPv4-notatie (`[::ffff:1.2.3.4]`, `[::127.0.0.1]`) wordt geweigerd, want
   in een shellscript is die niet betrouwbaar te herleiden en geen Hub heeft zo'n adres nodig; een schema in
   hoofdletters, een poort met voorloopnul, lege labels, niet-ASCII en meerregelige waarden ook. Het gevolg van te
   streng zijn is alleen: geen link. Er is **geen ontwikkelvlag** voor loopback: my_house heeft die niet nodig.
5. **De DNS-regel van §3.2 heeft in my_house niets te bewaken.** "Bij een hostnaam gelden de verboden bereiken ook voor
   het opgeloste adres, bij elke verbinding" gaat over verbindingen die een dienst zelf met de Hub maakt. my_house maakt
   nooit verbinding met `HUB_URL`: het adres is alleen het doel van een link die de browser van de gebruiker volgt
   (geen redirect te volgen, geen proxy, geen token). Een DNS-controle in de browser is bovendien niet mogelijk. Mocht
   my_house ooit zelf met de Hub praten (P3, C1.3-MH-04), dan geldt de regel daar volledig.
6. **Peil 1.1.1 in de app** (alleen uiterlijk; functies, routes, teksten en API gelijk):
   - nieuwe `-soft`-waarden voor verwarmen en koelen (tekst in `-ink` op de schemaband ≥ 7:1, was 4,9–6,2:1), in de
     web-app via de gekopieerde `ontwerptaal.css` en aliaslaag, op Android via `PeilTokens.kt`;
   - de rand van een gekozen modus en een brandende lichttegel in `domain-*-ink` (§4.1 regel 11; Android deed dat al);
   - ondernavigatie (web) op labelbreedte, minimaal 44 px, het actieve item **zonder extra gewicht** en met
     `aria-current="page"`, ook "Meer" op een pagina uit de Meer-sheet (E16); Android: actief = indicator + inkt, ook
     zonder extra gewicht;
   - de my_house-correcties uit PEIL_MIGRATIE §8.1 staan nu in de aliaslaag van de bron (E14). Voor de meeste
     selectors wint in my_house de laag `components` toch. Zichtbaar verandert alleen de arcering van een overgenomen
     schemadeel (`.week-bar .carried`) en van een uitgezette lichtkoppeling (`.lt-map.is-off`): die komt nu uit de
     aliaslaag (`--peil-stale-hatch`, grijs in plaats van wit), zoals Peil 1.1.1 voorschrijft (dimmen = arcering).
   "Verzonden" (E13) gebruikte my_house al.
7. **EVIDENCE**: `validatie` alleen uit de vaste lijst, toelichting in `opmerking`; regels voor elk punt van §8 van de
   conformiteitslijst, ook de latere fasen (als `Niet gestart` / `Niet getest`); `REVIEW-my_house-peil-1.1.1` voor de
   ontwerpreview. Nooit `Gereed` of `Geslaagd`.

## Afgewezen alternatieven

- **Drifttest die faalt bij elke nieuwe bronstand van het contract.** De conformiteitslijst verandert vaak; dan zou de
  CI van my_house rood worden van werk in een andere repo. Het contract vraagt een vaste bron-commit.
- **Alleen een vaste hash in de test (zonder vergelijking met de bron).** Dan bewijst niets dat de gepinde hash ook echt
  de bron op die commit is. `vergelijk --online` in CI (en lokaal met een checkout) sluit dat gat.
- **De DNS-regel nabootsen in het startscript** (`getent hosts` bij de start). Dat zegt niets over het moment dat de
  browser de link volgt (ander netwerk, andere resolver) en zou de start laten afhangen van DNS. Afgewezen; zie punt 5.
- **Android-ondernavigatie met een balk zoals op het web.** Material 3 heeft een indicator; ADR-0010 koos die al.
  Alleen het extra gewicht verdwijnt.

## Gevolgen

- Nieuwe Peil- of contractversie = `bijwerken` + de eigen tests (web, browser, Android met Paparazzi opnieuw vastleggen),
  nooit met de hand.
- De CI-backendjob heeft internet nodig voor `vergelijk --online` (raw.githubusercontent.com); een onbereikbare bron is
  voor de gepinde commit een fout, voor de huidige stand een waarschuwing.
- Open voor P3 (niet in deze ronde): alles-in-één image met healthcheck volgens §2.2 (C1.3-MH-03), bewaakt schrijfpad
  met de vingerafdruk over alle policy-sleutels (C1.3-MH-04, let op RV8), nieuwe routes volgens §5/§6 (C1.3-MH-05) en het
  manifest (C1.3-MH-06).
- **HARDE EIS (§2.3 regel 7):** niets hiervan raakt kijkgeschiedenis of MediaGuard-voortgang; my_house heeft geen
  mediaserver- of MediaGuard-koppeling.
