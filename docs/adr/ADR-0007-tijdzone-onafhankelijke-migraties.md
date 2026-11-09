# ADR-0007 — Databasemigraties rekenen tijdzone-onafhankelijk (reparatie migratie 0004)

- **Status:** aangenomen (testfout-reparatie, 2026-11-09)
- **Context-ID's:** geen eigen Excel-ID (testbetrouwbaarheid); bevinding uit `.lokaal/testcontrole/BEVINDINGEN.md`
  (rapport en controle my_house, test `tests/test_gapfix_security.py::test_migration_0004_limits_legacy_phone_tokens`)

## Context

Migratie 0004 geeft oude telefoonsleutels zonder vervaldatum er een: 180 dagen na het laatste gebruik, minstens 30
dagen na de update. De SQL rekende `timestamptz + interval '180 days'` en `now() + interval '30 days'`. PostgreSQL
telt het dagdeel van een interval op in de **wandkloktijd van de sessietijdzone**. Daardoor:

- Op een database in UTC (de CI-service) is de uitkomst precies 180 × 24 uur.
- Op een database met een andere tijdzone (compose zet de db-container op `TZ=Europe/Amsterdam`, en een NAS hoeft
  niet in UTC te draaien) verschuift de vervaldatum een uur zodra de periode een zomer-/wintertijdovergang kruist.
  In Europe/Amsterdam gebeurt dat op het grootste deel van het jaar.
- De app zelf rekent `CLIENT_TTL = timedelta(days=180)` in Python: precies 180 × 24 uur (`deps.py`, `pairing.py`).

De test eiste 5 minuten tolerantie op precies 179 dagen en faalde dus tegen elke database buiten UTC. Het verschil
ging om hooguit een uur op 180 dagen, maar de uitkomst hing af van een serverinstelling en week af van `CLIENT_TTL`.
Dat is een kleine echte fout in de migratie, niet alleen een zwakte van de test.

## Besluit

1. **Migratie 0004 rekent in verstreken tijd:** `interval '4320 hours'` (180 dagen) en `interval '720 hours'`
   (30 dagen). PostgreSQL telt uren altijd als verstreken tijd op, in elke sessietijdzone. Dat is gelijk aan
   `CLIENT_TTL`. Het audit-logboek en de rij krijgen dezelfde uitkomst als voorheen in UTC.
2. **De migratie wordt in place aangepast en er komt geen 0005.** Revisie 0004 zit in geen gepubliceerde release:
   v0.1.0 bevat alleen 0001, en 0.2.0 is niet gepubliceerd (release-run faalde). Een database die 0004 al lokaal
   heeft gedraaid, behoudt zijn datums. Het verschil is daar hooguit een uur. Schema, revisie-ID's, API en datamodel
   veranderen niet, en de OpenAPI-export blijft byte-gelijk.
3. **De test is tijdzone-onafhankelijk en bewijst dat ook.** Hij draait de migratie in drie sessietijdzones (`UTC`,
   `Europe/Amsterdam`, `Australia/Sydney`) via `ALTER DATABASE … SET timezone` op de tijdelijke database. Hij eist
   een exacte gelijkheid met 180 × 24 uur. Eén sleutel krijgt een laatste gebruik waarvan de 180 dagen over een
   wijziging van de UTC-offset lopen. Op elke dag van het jaar bestaat zo'n moment binnen 1 tot 149 dagen terug: dat
   is nagerekend voor zes jaar en vijf zones met zomertijd. De test is daarmee deterministisch en niet afhankelijk
   van de datum of van de serverinstelling.
4. **Regel voor nieuwe migraties en SQL:** een vaste looptijd op `timestamptz` schrijf je als uren (of je rekent in
   Python met een tijdzonebewuste `datetime`), niet als `interval 'N days'`. Kalenderdagen gebruik je alleen bewust,
   met de woningtijdzone expliciet erbij (zoals de energie-aggregaten).

## Afgewezen alternatieven

- **Alleen de test versoepelen** (meer dan een uur tolerantie of vergelijken in kalenderdagen): dan blijft de uitkomst
  afhankelijk van de serverinstelling en wijkt hij af van `CLIENT_TTL`.
- **De testdatabase of machine op UTC zetten:** dat verbergt het probleem. Een NAS met een andere tijdzone gaf dan
  nog steeds een afwijkende uitkomst.
- **`AT TIME ZONE 'UTC'` heen en terug:** werkt ook, maar is lastiger te lezen dan een interval in uren.
