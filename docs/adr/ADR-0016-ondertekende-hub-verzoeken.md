# ADR-0016 — Verificatie van ondertekende Hub-verzoeken

Status **geaccepteerd door de regisseur**, 19 november 2026, S-P3-740.

## Context

my_house gebruikt voor het bewaakte schrijfpad een door de beheerder bevestigde publieke
Hub-sleutelset. Het platformcontract beschrijft pinning in §4.5 en verificatie van het
`ExecutionRequest` in §8. ADR-0035 §B van ai-server-manager legt vast hoe de beheerder een
sleutelrotatie per app bevestigt.

## Besluiten

1. De beheerder vergelijkt de berekende vingerafdruk met die van de bevestigde Hub. De
   vingerafdruk omvat alle JWT- en policy-sleutels, ook de tweede sleutel tijdens overlap.
   Voor rotatie toont my_house de vorige en de berekende vingerafdruk. Elke verklaring
   moet in de globale volgorde volgen en geldig zijn onder een op dat moment gepinde
   sleutel van hetzelfde doel. De volledige set en volgorde worden in één transactie
   vastgelegd na bevestiging. De beheerder gebruikt de duurzaam opgeslagen waarden
   voor de bevestiging per instantie in de Hub. Ophalen van JWKS geeft geen vertrouwen.
2. Een uitvoeringsverzoek wordt met de actuele gepinde policy-sleutels geverifieerd.
   Alleen een geldige handtekening over de canonieke envelope geeft toegang tot de
   volgende controles. De controle gebruikt UTC en een geldigheidsduur van hooguit
   vijftien minuten. Zonder pin geeft de verificatiekern `501 capability_unavailable`.
3. Het verzoek wordt geregistreerd met een unieke nonce. De idempotentiesleutel geldt
   per route en geauthenticeerde aanroeper. Een exacte herhaling krijgt het opgeslagen
   antwoord, inclusief HTTP-status. Een andere envelope bij dezelfde sleutel geeft
   `422 idempotency_key_reused`. Op een opgeslagen herhaling zet de route
   `Idempotent-Replay: true`. Nonce, antwoord en eventuele opdracht in de eigen
   database delen een transactie. De route retourneert pas na de commit.
4. Een applicatieback-up draagt geen connectorvertrouwen over. Een logisch herstel
   wist de pin, het nonce-register en de idempotentieantwoorden. De beheerder bevestigt
   daarna de actuele Hub-set opnieuw. Nieuwe verzoeken moeten vanaf de eerste volledige
   seconde na die bevestiging zijn uitgegeven. Bij terugzetten van een volledige
   databasesnapshot of datamap moet vóór de start van een oudere worker eerst de wachtrij op
   [de offline kopie worden voorbereid](../onderhoud.md#offline-terugvalkopie-voorbereiden).
   Voor de koppeling geldt bovendien de gekoppelde-instantieprocedure uit ADR-0035 §B:
   verifieer dat de laatst bevestigde pin behouden is of trek de oude koppeling in en
   bevestig opnieuw voordat het schrijfpad beschikbaar komt.

## Gevolgen

S-P3-742 sluit de eigenaar-routes aan en dwingt zelfstandig de lokale allowlist,
parameterschema's, risicoklasse, goedkeuring, gewenste versie, readback en bestaande
apparaatbeperkingen af. De onafhankelijke lokale controles horen in de transactie
vóór het inplannen. De ondertekende `risk_claim` heeft geen invloed op het lokale
beleid. S-P3-745 sluit de beheerbevestiging en de weergave van de opgeslagen
vingerafdruk en rotatievolgorde aan.

## Meldingen bij het contract

1. **`policy_decision.policy_hash` (contract 1.3 §8).** §8 beschrijft voor de
   executor een vergelijking met een onafhankelijke, alleen-lezen beleidskopie
   en verwijst daar bij eigenaar-connectors naar. my_house controleert het formaat
   `sha256:<64 hextekens>` en de binding aan de envelope via de handtekening van
   een gepinde policy-sleutel. my_house handhaaft zijn lokale actiebeleid zelfstandig.
   Er komt geen onafhankelijke beleidsreferentie voor de Hub-hash. Geaccepteerd
   als contractmelding (optie B van de regisseur in S-P3-740), volgens de opbouw
   van home-control-hub ADR-0016, ‘Meldingen bij het contract’.
