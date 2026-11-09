Smart Home Control: één lokale app voor Hue, Gree-airco, HomeWizard en Zigbee, als TrueNAS Custom App, met web-app en Android-app.

**Let op:** nog niet getest met echte apparaten. Elke adapter is getest tegen nep-apparaten die het echte protocol spreken, op basis van de officiële documentatie; in de app staan apparaten op "nog niet getest" tot ze werken. Zie `docs/testlog.md`.

## Installeren en bijwerken

- **Server (TrueNAS SCALE 25.10 of Linux):** volg `docs/installatie-truenas.md`. De images en hoe je de handtekening controleert, staan onderaan.
- **Bijwerken:** maak eerst een back-up. Bij het starten maakt de API zelf een database-dump in `backups/pre-migrate-….sql.gz` voordat hij de database bijwerkt. Terugdraaien: `docs/onderhoud.md`.
- **Eerst de server, dan de app.** De nieuwe Android-app herkent een oudere server niet op het thuisadres (de tunnel blijft werken).
- **Android:** download de APK hieronder en installeer (toestaan: *installeren uit onbekende bron*). Koppel via **Account › Telefoons** in de web-app. Had je de testversie 0.1.0 met de tijdelijke sleutel? Verwijder die eerst en koppel opnieuw.

## Wat is er nieuw (verbeterronde)

- **Installatie:** installatiewizard (`/installatie`) met controles vooraf, een helppagina per fabrikant (`/help`), en een formulier *Woning aanmaken* als er nog geen woning is. Thuis inloggen en live-updates werken ook met de tunnel aan.
- **Klimaat:** weekschema's met modus per blok (een blok zonder modus zet nooit een uitgeschakelde unit aan), comfortprofielen, een waakhond per zone, en experimenten die zelf van profiel wisselen. Commando's zijn pas "bevestigd" na uitlezing van de unit; een mislukt commando wordt opnieuw geprobeerd en zet nooit de verwarming uit.
- **Energie:** uurtotalen (snelle pagina's, ook na jaren), plausibiliteitscontrole van meterstanden, correcte zomer- en wintertijd, tarieven met T1/T2 en gas, kwaliteitslabels, vorige/volgende periode en een gestreamde export (uur, dag of ruw).
- **Back-ups:** automatische back-ups instellen in de app (wachtwoordzin, tijdstip, aantal, optioneel dagelijks een database-dump), inhalen na uitval, melding bij mislukken, herstellen en verhuizen via de app, en `shc-api restore-drill` om herstel te oefenen. Zie `docs/onderhoud.md`.
- **Meldingen:** melding als de achtergronddienst niet reageert, en optioneel een push-bericht buiten de app (ntfy) als de worker of de back-ups stilvallen.
- **Beveiliging:** gebruikersbeheer en logboek (**Account**), telefoonrollen (bedienen, alleen kijken, beheren), telefoon-sleutels die na 180 dagen verlopen, beheer van gebruikers/telefoons/back-ups alleen in de web-app met wachtwoord, serverherkenning met controlevraag in de Android-app, certificaatcontrole vóór het versturen van een sleutel, strengere inlogrem, drie gescheiden containernetwerken.
- **Hue:** knoppen koppelen aan licht- en klimaatacties (met simuleren), een robuustere eventstream, en verwijderde lampen en scènes worden opgeruimd.
- **Software:** Python-pakketten uit een lock-bestand met hashes, wekelijkse audit, images ondertekend met cosign, en een release komt pas uit als alle tests (ook Android) slagen.

## Database-updates

- **0004:** telefoons die vóór versie 0002 zijn gekoppeld en nooit een vervaldatum kregen, vervallen nu 180 dagen na hun laatste gebruik (minstens 30 dagen na de update) en krijgen rol *Bedienen*. In het logboek staat per telefoon een regel. Wil je dat zo'n telefoon weer beheert, koppel hem dan opnieuw met rol *Beheren*.

De APK is ondertekend met de eigen release-sleutel uit de GitHub-geheimen, geschikt om zelf te installeren (niet via de Play Store). Zonder ingestelde sleutel bevat de release geen APK; zie `docs/installatie-truenas.md`.
