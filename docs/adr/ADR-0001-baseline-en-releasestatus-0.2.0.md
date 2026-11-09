# ADR-0001 — Baseline en releasestatus: delta tegen HEAD 0.2.0

- **Status:** aanvaard (fase P0, 2026-11-09)
- **Context-ID's:** B06-001, D07, `PLATFORM_CONTRACTEN.md` 1.1 §11–§12
- **Nummering:** de besluiten 1–12 in `docs/architectuur.md` blijven bestaan; ADR's voor TrueNAS Home Platform V2
  beginnen bij ADR-0001 in `docs/adr/`.

## Context

Het uitvoeringspakket (`02_BOUWPLANNEN/B06_MY_HOUSE_DELTA.md`, `03_INSTALLATIE/I05_MY_HOUSE.md`) noemt
"my_house v0.1.0" als referentie. De werkelijke code-HEAD is `b0a6a98` met `VERSION` 0.2.0. Uit alleen-lezen
controles op GitHub blijkt (zie `docs/platform-v2/INVENTARIS.md` §1):

- tag en GitHub-release bestaan alleen voor **v0.1.0**;
- de release-run voor 0.2.0 (run 42580532662) faalde in job `apk` op een netwerkfout (Gradle-download HTTP 504),
  waardoor `images` en `publish` zijn overgeslagen — 0.2.0 is dus **niet** als image of release gepubliceerd;
- GHCR-images zijn niet anoniem leesbaar, digests zijn onbekend;
- welke versie op de NAS draait is onbekend (D07).

## Besluit

1. De delta-matrix en alle P3-bouwwerk gaan tegen **HEAD 0.2.0**, niet tegen v0.1.0. Wat 0.2.0 al toevoegde staat in
   `INVENTARIS.md` §1 en telt als *bestaand*.
2. De bouwer tagt of publiceert **niets** zelf en wijzigt `VERSION` niet in P0 (een push van `VERSION` naar `main`
   start `release.yml`). Het opnieuw starten van de mislukte release-run is een handeling voor de eigenaar.
3. Geen enkele B06-taak krijgt `Gereed`; alles wat alleen met nep-apparaten/simulators is getest blijft `Bezig` met
   validatie `Sim-getest` of `NIET GEVALIDEERD (hardware / echte NAS)`.

## Gevolgen

- De Hub-adapter (B02/B06-006) moet de versie van my_house **meten** (`GET /api/v1/health` → `version`, later
  `/connector/v1/discover` → `source_version`) en mag niet aannemen dat 0.2.0 draait.
- Een productie-installatie van 0.2.0 kan pas na een geslaagde release-run (images + digests).
